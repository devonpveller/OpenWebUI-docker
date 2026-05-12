from .db import MarkdownFileIndexer, Page, Section, XmlFileIndexer, TANTIVY_AVAILABLE
from .crawl import crawl_target, crawl_target_sync, SmolCrawler
from .augment import augment_markdown, augment_pages
from .owui_client import OwuiConfig, OwuiKnowledgeClient, SyncResult
import typer
import asyncio
from pathlib import Path
from typing import List, Literal, Optional
from loguru import logger
from .utils import get_storage_path
import os

# Optional tantivy import
if TANTIVY_AVAILABLE:
    from .db import TantivyIndexer
else:
    TantivyIndexer = None

app = typer.Typer()


@app.command()
def crawl(target_url: str = typer.Argument(..., help="The URL to crawl.")) -> List[Page]:
    """Crawls a target URL and returns the extracted pages."""
    logger.info(f"Crawling {target_url}")
    results = asyncio.run(crawl_target(target_url))
    logger.success(f"Crawled {len(results)} pages from {target_url}")
    return results


@app.command()
def index(
    target_url: str = typer.Argument(..., help="The URL to crawl."),
    name: str = typer.Argument(..., help="The name of the index to create or update."),
    index_type: str = typer.Option(
        "markdown", help="The type of index to create ('search' for Tantivy, 'markdown' for files, 'xml' for a single XML file)."
    ),
) -> List[Page]:
    """Crawls a target URL and indexes the content into the specified index."""
    logger.info(f"Indexing {target_url} into {name}")
    crawl_results = crawl(target_url)
    if index_type == "search":
        if not TANTIVY_AVAILABLE:
            logger.error("Tantivy not installed. Use: pip install smolcrawl[full]")
            raise typer.Exit(1)
        indexer = TantivyIndexer(name)
        logger.info(f"Adding {len(crawl_results)} pages to {name}")
        indexer.add_pages(crawl_results)
        logger.success(f"Indexed {len(crawl_results)} pages into {name}")
    elif index_type == "markdown":
        indexer = MarkdownFileIndexer(name)
        logger.info(f"Writing {len(crawl_results)} pages to markdown files in {name}")
        indexer.add_pages(crawl_results)
        logger.success(f"Wrote {len(crawl_results)} pages to markdown files in {name}")
    elif index_type == "xml":
        indexer = XmlFileIndexer(name)
        logger.info(f"Writing {len(crawl_results)} pages to XML file {indexer.target_file}")
        indexer.add_pages(crawl_results)
        logger.success(f"Wrote {len(crawl_results)} pages to {indexer.target_file}")
    return crawl_results


@app.command()
def list_indices() -> None:
    """Lists the available Tantivy (search) indices."""
    if not TANTIVY_AVAILABLE:
        logger.warning("Tantivy not installed. Use: pip install smolcrawl[full]")
    for f in os.listdir(os.path.join(get_storage_path(), "db")):
        print(f)


@app.command()
def delete_index(name: str = typer.Argument(..., help="The name of the Tantivy index to delete.")) -> None:
    """Deletes the specified Tantivy (search) index."""
    import os

    os.remove(os.path.join(get_storage_path(), "db", name))
    logger.success(f"Deleted index {name}")


@app.command()
def query(
    index_name: str = typer.Argument(..., help="The name of the Tantivy (search) index to query."),
    query: str = typer.Argument(..., help="The search query string."),
    limit: int = typer.Option(10, help="The maximum number of results to return."),
    score_threshold: float = typer.Option(
        0.5, help="The minimum score for results to be included."
    ),
) -> None:
    """Queries a Tantivy index and prints the results."""
    if not TANTIVY_AVAILABLE:
        logger.error("Tantivy not installed. Use: pip install smolcrawl[full]")
        raise typer.Exit(1)
    
    indexer = TantivyIndexer(index_name)

    res = list(indexer.query(query, limit=limit, score_threshold=score_threshold))
    logger.success(f"Found {len(res)} results")
    for r in res:
        logger.info(f" - {r.title} / {r.url}")


@app.command()
def augment(
    input_dir: str = typer.Argument(..., help="Directory of markdown files to augment."),
    output_dir: str = typer.Option(None, help="Output directory (default: <input_dir>_augmented)."),
) -> None:
    """Augment markdown files with RAG metadata headers. No OWUI required."""
    input_path = Path(input_dir)
    if not input_path.exists():
        logger.error(f"Input directory not found: {input_dir}")
        raise typer.Exit(1)

    out_path = Path(output_dir) if output_dir else Path(f"{input_dir}_augmented")
    out_path.mkdir(parents=True, exist_ok=True)

    count = 0
    for md_file in input_path.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        rel = md_file.relative_to(input_path)
        augmented = augment_markdown(content, source_url=str(rel), doc_title=md_file.stem)
        dest = out_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(augmented, encoding="utf-8")
        count += 1

    logger.success(f"Augmented {count} files → {out_path}")


@app.command("owui-sync")
def owui_sync(
    input_dir: str = typer.Argument(..., help="Directory of markdown files to upload."),
    owui_url: str = typer.Option("http://localhost:3000", help="Open WebUI base URL."),
    owui_api_key: str = typer.Option(..., envvar="OWUI_API_KEY", help="OWUI API key."),
    kb_name: str = typer.Option("SmolCrawl Docs", help="Knowledge base name."),
    concurrency: int = typer.Option(3, help="Upload concurrency."),
) -> None:
    """Upload a directory of markdown files to an OWUI knowledge base."""
    input_path = Path(input_dir)
    if not input_path.exists():
        logger.error(f"Input directory not found: {input_dir}")
        raise typer.Exit(1)

    # Build Page objects from markdown files
    pages = []
    for md_file in input_path.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        rel = md_file.relative_to(input_path)
        pages.append(Page(
            url=str(rel),
            title=md_file.stem,
            content=content,
            raw_html="",
        ))

    if not pages:
        logger.warning("No markdown files found.")
        raise typer.Exit(1)

    logger.info(f"Found {len(pages)} markdown files to sync")

    config = OwuiConfig(
        base_url=owui_url,
        api_key=owui_api_key,
        knowledge_base_name=kb_name,
        upload_concurrency=concurrency,
    )
    with OwuiKnowledgeClient(config) as client:
        result = client.sync_pages(
            pages, kb_name,
            on_progress=lambda cur, tot, name: logger.info(f"[{cur}/{tot}] {name}"),
        )

    logger.success(
        f"Sync complete: {result.uploaded} uploaded, "
        f"{result.skipped} skipped, {result.failed} failed"
    )
    if result.errors:
        for err in result.errors:
            logger.error(f"  {err}")


@app.command("owui-pipeline")
def owui_pipeline(
    url: str = typer.Argument(..., help="URL to crawl."),
    owui_url: str = typer.Option("http://localhost:3000", help="Open WebUI base URL."),
    owui_api_key: str = typer.Option(..., envvar="OWUI_API_KEY", help="OWUI API key."),
    kb_name: str = typer.Option("", help="Knowledge base name (auto-generated from domain if empty)."),
    server_intensity: float = typer.Option(0.3, help="Crawl intensity 0.0-1.0."),
    max_pages: int = typer.Option(500, help="Maximum pages to crawl."),
    no_augment: bool = typer.Option(False, help="Skip RAG augmentation step."),
    concurrency: int = typer.Option(3, help="Upload concurrency."),
) -> None:
    """Full pipeline: crawl → augment → upload to OWUI knowledge base."""
    from urllib.parse import urlparse

    # Auto-generate KB name from domain
    if not kb_name:
        domain = urlparse(url).netloc
        kb_name = f"SmolCrawl - {domain}"

    # Step 1: Crawl
    logger.info(f"Step 1/3: Crawling {url} (max {max_pages} pages, intensity {server_intensity})")
    max_concurrent = max(1, int(1 + (server_intensity * 11)))
    delay = (1.0 - server_intensity) * 2.0
    pages = crawl_target_sync(url, max_pages=max_pages, max_concurrent=max_concurrent, delay=delay)
    logger.success(f"Crawled {len(pages)} pages")

    # Step 2: Augment
    if not no_augment:
        logger.info(f"Step 2/3: Augmenting {len(pages)} pages for RAG")
        pages = augment_pages(pages)
        logger.success(f"Augmented {len(pages)} pages")
    else:
        logger.info("Step 2/3: Skipping augmentation (--no-augment)")

    # Step 3: Upload
    logger.info(f"Step 3/3: Uploading to OWUI KB '{kb_name}'")
    config = OwuiConfig(
        base_url=owui_url,
        api_key=owui_api_key,
        knowledge_base_name=kb_name,
        upload_concurrency=concurrency,
    )
    with OwuiKnowledgeClient(config) as client:
        result = client.sync_pages(
            pages, kb_name,
            on_progress=lambda cur, tot, name: logger.info(f"[{cur}/{tot}] {name}"),
        )

    logger.success(
        f"Pipeline complete: {len(pages)} crawled, "
        f"{result.uploaded} uploaded, {result.skipped} skipped, "
        f"{result.failed} failed"
    )
    if result.errors:
        for err in result.errors:
            logger.error(f"  {err}")


if __name__ == "__main__":
    app()
