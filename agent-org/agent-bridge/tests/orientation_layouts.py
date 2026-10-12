"""ao-orient round 4: the five constructed public-style repo layouts of the cost check
(copied from findings/ao-orient-justification-2026-10-12.md, scratch `cost.py`). In-memory
{path: content}; served by tests/memgh.py. Names are ASCII except where a layout is about i18n."""

# ── constructed public-style layouts ──────────────────────────────────────────────────────────
DOTNET = {
    "eShop.sln": "x", "eShop.Web.slnf": "x", "global.json": "{}", "Directory.Packages.props": "x",
    "Directory.Build.props": "x", "nuget.config": "x", ".editorconfig": "x",
    "src/Catalog.API/Catalog.API.csproj": "x", "src/Catalog.API/Program.cs": "x",
    "src/Catalog.API/Apis/CatalogApi.cs": "x", "src/Catalog.API/Model/CatalogItem.cs": "x",
    "src/Catalog.API/Setup/catalog.json": "x", "src/Catalog.API/Pics/1.webp": "x",
    "src/Basket.API/Basket.API.csproj": "x", "src/Basket.API/Proto/basket.proto": "x",
    "src/Basket.API/Grpc/BasketService.cs": "x",
    "src/Ordering.API/Ordering.API.csproj": "x",
    "src/Ordering.API/Application/Commands/CreateOrderCommandHandler.cs": "x",
    "src/Ordering.Domain/AggregatesModel/OrderAggregate/Order.cs": "x",
    "src/Ordering.Infrastructure/Migrations/20240106121712_Initial.cs": "x",
    "src/Ordering.Infrastructure/Migrations/20240106121712_Initial.Designer.cs": "x",
    "src/WebApp/WebApp.csproj": "x", "src/WebApp/Components/Pages/Item/ItemPage.razor": "x",
    "src/WebApp/Components/_Imports.razor": "x", "src/WebApp/wwwroot/css/app.css": "x",
    "src/eShop.AppHost/eShop.AppHost.csproj": "x", "src/eShop.AppHost/Program.cs": "x",
    "src/eShop.ServiceDefaults/Extensions.cs": "x",
    "src/HybridApp/Platforms/Android/MainActivity.cs": "x",
    "tests/Basket.UnitTests/Basket.UnitTests.csproj": "x",
    "tests/Basket.UnitTests/BasketServiceTests.cs": "x",
    "tests/Catalog.FunctionalTests/Catalog.FunctionalTests.csproj": "x",
    "tests/Catalog.FunctionalTests/CatalogApiTests.cs": "x",
    "tests/Ordering.UnitTests/Ordering.UnitTests.csproj": "x",
    "tests/Ordering.UnitTests/Domain/OrderAggregateTest.cs": "x",
    "tests/Ordering.UnitTests/Application/NewOrderRequestHandlerTest.cs": "x",
    ".github/workflows/pr-validation.yml": "x", "build/acr-build/queue-all.ps1": "x",
    "README.md": ("# eShop Reference Application - \"AdventureWorks\"\n\nA reference .NET app.\n\n"
                  "## Getting started\n### Prerequisites\n### Running the solution\n"
                  "### Azure Open AI\n### Use Azure Developer CLI\n## Contributing\n"
                  "## Sample data\n### Visual Studio 2022 (Windows) + .NET Aspire\n"),
}
NODE = {
    "package.json": '{"private":true,"scripts":{"build":"turbo run build","test":"turbo run test"}}',
    "pnpm-workspace.yaml": "x", "turbo.json": "{}", ".npmrc": "x",
    ".changeset/config.json": "{}", ".changeset/funny-dogs-run.md": "x",
    "apps/web/package.json": '{"scripts":{"test":"vitest run"}}', "apps/web/next.config.mjs": "x",
    "apps/web/app/layout.tsx": "x", "apps/web/app/page.tsx": "x",
    "apps/web/app/(marketing)/pricing/page.tsx": "x", "apps/web/app/blog/[slug]/page.tsx": "x",
    "apps/web/app/api/auth/[...nextauth]/route.ts": "x", "apps/web/app/@modal/(.)photo/[id]/page.tsx": "x",
    "apps/web/__tests__/page.test.tsx": "x", "apps/web/__tests__/pricing.test.tsx": "x",
    "apps/web/tsconfig.json": "{}",
    "apps/docs/package.json": '{"scripts":{"test":"echo \\"Error: no test specified\\" && exit 1"}}',
    "apps/docs/pages/index.mdx": "x", "apps/docs/pages/getting-started.mdx": "x",
    "packages/ui/package.json": '{"name":"@acme/ui","scripts":{"test":"vitest"}}',
    "packages/ui/src/button.tsx": "x", "packages/ui/src/button.test.tsx": "x",
    "packages/ui/src/card.tsx": "x", "packages/ui/src/card.spec.tsx": "x",
    "packages/eslint-config/index.js": "x", "packages/eslint-config/package.json": '{"name":"@acme/eslint-config"}',
    "packages/typescript-config/base.json": "{}",
    "packages/utils/src/format-date.ts": "x", "packages/utils/src/format-date.test.ts": "x",
    "packages/utils/package.json": '{"scripts":{"test":"vitest run"}}',
    "README.md": ("# Turborepo starter\n\nThis is an official starter.\n\n## Using this example\n"
                  "## What's inside?\n### Apps and Packages\n### Utilities\n### Build\n### Develop\n"
                  "### Remote Caching\n## Useful Links\n"),
}
PYI18N = {
    "pyproject.toml": "[project]\nname='paquete'\n[tool.pytest.ini_options]\ntestpaths=['tests']\n",
    "src/paquete/__init__.py": "x", "src/paquete/núcleo.py": "x", "src/paquete/cli.py": "x",
    "src/paquete/análisis/estadística.py": "x", "src/paquete/io/lectura.py": "x",
    "tests/test_núcleo.py": "x", "tests/test_cli.py": "x", "tests/conftest.py": "x",
    "tests/datos/ejemplo_año.csv": "x",
    "docs/es/guía.md": "x", "docs/zh/指南.md": "x", "docs/index.md": "x",
    "CHANGELOG.md": "x", "LICENSE": "x",
    "README.md": ("# Paquete de análisis 📊\n\n"
                  "[![CI](https://github.com/x/y/actions/workflows/ci.yml/badge.svg)](x)\n\n"
                  "## ✨ Características\n## 🚀 Inicio rápido\n## 📦 Instalación\n"
                  "### Con pip\n### Con conda\n## Uso\n## Configuración — variables de entorno\n"
                  "## 许可证\n## 中文文档\n## Contribuir 🤝\n## License\n"),
}
JAVA = {
    "build.gradle.kts": "x", "settings.gradle.kts": "x", "gradlew": "x", "gradlew.bat": "x",
    "gradle/wrapper/gradle-wrapper.properties": "x",
    "src/main/java/com/example/demo/DemoApplication.java": "x",
    "src/main/java/com/example/demo/web/OrderController.java": "x",
    "src/main/java/com/example/demo/service/impl/OrderServiceImpl.java": "x",
    "src/main/resources/application.yml": "x", "src/main/resources/db/migration/V1__init.sql": "x",
    "src/test/java/com/example/demo/DemoApplicationTests.java": "x",
    "src/test/java/com/example/demo/web/OrderControllerTest.java": "x",
    "src/test/resources/application-test.yml": "x",
    "README.md": "# Demo Service\n## Build & Run\n## API (v1)\n## Configuration\n### `application.yml`\n## FAQ\n",
}
GOI18N = {
    "go.mod": "module example.com/svc\n", "go.sum": "x", "Makefile": "test:\n\tgo test ./...\n",
    "cmd/server/main.go": "x", "cmd/migrate/main.go": "x",
    "internal/http/handler.go": "x", "internal/http/handler_test.go": "x",
    "internal/store/postgres/store.go": "x", "internal/store/postgres/store_test.go": "x",
    "pkg/client/client.go": "x", "pkg/client/client_test.go": "x",
    "docs/日本語/README.md": "x", "docs/ko-KR/설치.md": "x", "docs/en/install.md": "x",
    "deploy/helm/svc/templates/deployment.yaml": "x", "deploy/helm/svc/values.yaml": "x",
    ".golangci.yml": "x", "Dockerfile": "x",
    "README.md": ("# svc — a small Go service\n## Getting Started\n## Configuration — env vars\n"
                  "## Running tests\n## 日本語のドキュメント\n## Roadmap / TODO\n## License: MIT\n"),
}
BUILT = {"dotnet-solution": DOTNET, "node-monorepo": NODE, "python-nonascii-readme": PYI18N,
         "java-gradle": JAVA, "go-i18n-docs": GOI18N}
