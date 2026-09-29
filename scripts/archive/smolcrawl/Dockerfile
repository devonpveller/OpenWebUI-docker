FROM ghcr.io/open-webui/pipelines:main

# Install Node.js for readabilipy content extraction
RUN apt-get update && apt-get install -y nodejs npm && rm -rf /var/lib/apt/lists/*

# Install SmolCrawl from local source (PyPI version is stale and pulls crawlee)
COPY pyproject.toml README.md /tmp/smolcrawl/
COPY src /tmp/smolcrawl/src
RUN pip install --no-cache-dir /tmp/smolcrawl && rm -rf /tmp/smolcrawl

# Fix readabilipy jsdom ESM compatibility – jsdom >=25 pulls html-encoding-sniffer v6
# which uses ES modules, but Node.js 18 (from apt) cannot require() ESM modules.
# Pin jsdom to v24 (last CJS-compatible version) to fix ExtractArticle.js failures.
RUN cd /usr/local/lib/python3.11/site-packages/readabilipy/javascript && npm install jsdom@24.1.3

# Copy the pipeline definition
COPY smolcrawl_pipeline.py /app/pipelines/

EXPOSE 9099
