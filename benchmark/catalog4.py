"""Round 4: fresh defects on the same pipelines, run against the classifier frozen as v4.

Round 3's misses shaped v4 (timestamps before anchored rules, compiler-style error lines, tool-aware lint and format steps,
NuGet and pytest-setup messages). Round 4 uses different defects on the same tools, plus a few failure styles not seen
before, to check that those fixes generalise rather than fit the exact logs they came from.
"""
GOY = "/.azure-pipelines/go.yml"
DNY = "/.azure-pipelines/dotnet.yml"
TESTS = "/.azure-pipelines/tests.yml"

D = []


def add(label, pipeline, edits, message, subtype="", ambiguous=False, note=""):
    D.append(dict(label=label, pipeline=pipeline, edits=edits, message=message, subtype=subtype, ambiguous=ambiguous, note=note))


def rep(path, old, new):
    return {"path": path, "old": old, "new": new}


def app(path, text):
    return {"path": path, "append": text}


def new(path, content):
    return {"path": path, "create": content}


# ------------------------------------------------------------------ Go
add("code-error", "click-go", [rep("go/cart.go", "func Discount(amount, percent int) int {\n\treturn amount - amount*percent/100",
                                   "func Discount(amount, percent int) int {\n\treturn \"discounted\"")],
    "Return a label from the discount", "go-type-mismatch")
add("code-error", "click-go", [rep("go/cart.go", "package cart\n", "package cart\n\nimport \"strings\"\n")],
    "Import strings for later", "go-unused-import")
add("lint", "click-go", [app("go/cart.go", "\nfunc isFree(i Item) bool {\n\treturn true\n\ti.Price = 0\n\treturn false\n}\n")],
    "Add a free item check", "go-vet-unreachable")
add("test-failure", "click-go", [rep("go/cart.go", "for _, it := range items {\n\t\tsum += it.Price * it.Qty", "for i := range items {\n\t\tit := items[i+1]\n\t\tsum += it.Price * it.Qty")],
    "Index the items directly", "go-test-panic")
add("network", "click-go", [rep(GOY, "- script: go build ./...", "- script: go mod download golang.org/x/text@v0.14.0\n        displayName: Warm the module cache\n        env:\n          GOPROXY: http://127.0.0.1:1\n          GOFLAGS: -mod=mod\n      - script: go build ./...")],
    "Warm the module cache through the local proxy", "go-refused-proxy")
add("code-error", "click-go", [rep("go/cart.go", "\treturn sum\n}", "}")],
    "Drop the extra return", "go-missing-return")

# ------------------------------------------------------------------ Java
add("code-error", "click-java", [rep("java/src/main/java/com/example/Cart.java", "import java.util.List;", "import java.util.List;\nimport org.acme.pricing.Rules;")],
    "Import the pricing rules", "java-missing-package")
add("config", "click-java", [rep("java/pom.xml", "<maven.compiler.source>17</maven.compiler.source>", "<maven.compiler.source>99</maven.compiler.source>")],
    "Raise the source level", "maven-invalid-release")
add("test-failure", "click-java", [rep("java/src/test/java/com/example/CartTest.java", "assertEquals(170, Cart.discount(200, 15));", "throw new IllegalStateException(\"price list not loaded\");")],
    "Fail fast when the price list is missing", "junit-thrown-exception")
add("test-failure", "click-java", [rep("java/src/test/java/com/example/CartTest.java", "assertEquals(13, Cart.total(", "assertEquals(14, Cart.total(")],
    "Correct the expected total", "junit-wrong-expectation")
add("dependency", "click-java", [rep("java/pom.xml", "<artifactId>junit-jupiter</artifactId>", "<artifactId>junit-jupiter-extras</artifactId>")],
    "Use the extras artifact", "maven-wrong-artifact")

# ------------------------------------------------------------------ .NET
add("code-error", "click-dotnet", [rep("dotnet/Cart/Cart.cs", "public record Item(int Price, int Qty);", "public record Item(int Price, int Qty);\n\npublic class Holder { public Money Value { get; set; } = default!; }")],
    "Add a holder for the price", "csharp-missing-type")
add("config", "click-dotnet", [rep("dotnet/Cart/Cart.csproj", "<TargetFramework>net8.0</TargetFramework>", "<TargetFramework>net99.0</TargetFramework>")],
    "Move to the next framework", "dotnet-invalid-framework")
add("test-failure", "click-dotnet", [rep("dotnet/Cart.Tests/CartTests.cs", "Assert.Equal(170, Calculator.Discount(200, 15));", "throw new InvalidOperationException(\"price list not loaded\");")],
    "Fail fast when the price list is missing", "xunit-thrown-exception")
add("dependency", "click-dotnet", [rep("dotnet/Cart.Tests/Cart.Tests.csproj", 'Include="Microsoft.NET.Test.Sdk" Version="17.11.1"', 'Include="Microsoft.NET.Test.Sdk" Version="99.0.0"')],
    "Move to the newest test SDK", "nuget-missing-sdk-version")
add("network", "click-dotnet", [rep(DNY, "- script: dotnet restore Cart.Tests/Cart.Tests.csproj", "- script: dotnet restore Cart.Tests/Cart.Tests.csproj --source http://127.0.0.1:1/index.json")],
    "Restore from the local feed", "nuget-refused-source")

# ------------------------------------------------------------------ Python (tests, ruff, mypy)
add("test-failure", "click-tests", [new("tests/test_discounts.py",
                                        "import pytest\n\n\n@pytest.mark.parametrize(\"amount,percent,expected\", [(200, 15, 170), (100, 10, 90), (50, 50, 20)])\n"
                                        "def test_discount(amount, percent, expected):\n    assert amount - amount * percent // 100 == expected\n")],
    "Add discount cases", "pytest-parametrized-failure")
add("dependency", "click-tests", [new("tests/test_hashing.py", "import xxhashh\n\n\ndef test_digest():\n    assert xxhashh.xxh32_hexdigest(b'a')\n")],
    "Add a hashing test", "pytest-collection-missing-module")
add("lint", "click-lint", [app("src/click/utils.py", "\n\ndef _is_missing(value: object) -> bool:\n    return value == None\n")],
    "Add a missing value check", "ruff-none-comparison")
add("type-error", "click-typecheck", [app("src/click/utils.py", "\n\ndef _twice(value: int) -> int:\n    return value * 2\n\n\n_RESULT = _twice(\"3\")\n")],
    "Double a default", "mypy-arg-type")

# ------------------------------------------------------------------ Node and containers
add("lint", "click-web", [rep("web/src/cart.js", "module.exports = { addItem, total, applyDiscount };", "module.exports = { addItem, total, applyDiscount, addItem };")],
    "Export the add helper twice", "eslint-duplicate-key")
add("test-failure", "click-web", [rep("web/src/cart.js", "return Math.round(amount * (1 - percent / 100) * 100) / 100;", "return Math.round(amount * (1 - rate / 100) * 100) / 100;")],
    "Use a named rate", "jest-reference-error")
add("dependency", "click-web", [rep("web/package.json", "\"typescript\": \"5.6.3\"", "\"typescript\": \"5.6.3\",\n    \"left-padd-internal\": \"2.0.0\"")],
    "Use the internal pad helper", "npm-missing-package")
add("config", "click-container", [rep("Dockerfile", "RUN pip install --no-cache-dir .", "COPY requirements.txt ./\nRUN pip install --no-cache-dir -r requirements.txt .")],
    "Install from a requirements file", "docker-missing-requirements")
add("config", "click-container", [rep("Dockerfile", "RUN pip install --no-cache-dir .", "RUN curl -fsSL https://example.com/setup.sh -o /tmp/setup.sh\nRUN pip install --no-cache-dir .")],
    "Fetch a setup script", "docker-missing-tool")

# ------------------------------------------------------------------ custom messages (no rule could know them)
add("other", "click-tests", [new("scripts/release_lock.py", "import sys\n\nsys.exit(\"deployment lock is held by build 1234\")\n"),
                             rep(TESTS, "- script: python -m pytest", "- script: python scripts/release_lock.py\n        displayName: Check release lock\n      - script: python -m pytest")],
    "Check the release lock first", "custom-exit-message")
add("other", "click-tests", [new("scripts/quota.py", "raise RuntimeError(\"quota exceeded for tenant acme\")\n"),
                             rep(TESTS, "- script: python -m pytest", "- script: python scripts/quota.py\n        displayName: Check tenant quota\n      - script: python -m pytest")],
    "Check the tenant quota first", "custom-exception")
