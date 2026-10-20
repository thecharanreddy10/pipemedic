"""Round 3: more ecosystems (Go, Java/Maven, .NET), fresh defects on existing tools, and failures nobody could expect a rule to know.

The classifier was frozen (v3) before any of this was built or run. Round 2's misses shaped v3; nothing in round 3 did.
Label "other" marks a failure with a custom, application-specific message: the right answer is "unknown" or a generic
script failure, and a specific wrong category is the failure mode being measured.
"""
from catalog2 import _job2  # noqa: F401

GO_SETUP = '''- task: GoTool@0
  displayName: Use Go 1.22
  inputs:
    version: "1.22"
'''
DOTNET_SETUP = '''- task: UseDotNet@2
  displayName: Use .NET 8
  inputs:
    packageType: sdk
    version: 8.0.x
'''

GO_YML = _job2("go", "Go build", """- script: go build ./...
  workingDirectory: go
  displayName: Build
- script: go vet ./...
  workingDirectory: go
  displayName: Vet
- script: go test ./...
  workingDirectory: go
  displayName: Test
- script: |
    test -z "$(gofmt -l .)" || { gofmt -l .; exit 1; }
  workingDirectory: go
  displayName: Format check
""", "Builds, vets and tests the Go cart package.", setup=GO_SETUP)

JAVA_YML = _job2("java", "Java build", """- script: mvn -B -ntp test
  workingDirectory: java
  displayName: Build and test
""", "Builds and tests the Java cart (Maven).", setup="")

DOTNET_YML = _job2("dotnet", ".NET build", """- script: dotnet restore Cart.Tests/Cart.Tests.csproj
  workingDirectory: dotnet
  displayName: Restore
- script: dotnet build Cart.Tests/Cart.Tests.csproj --no-restore
  workingDirectory: dotnet
  displayName: Build
- script: dotnet test Cart.Tests/Cart.Tests.csproj --no-build
  workingDirectory: dotnet
  displayName: Test
- script: dotnet format Cart/Cart.csproj --verify-no-changes
  workingDirectory: dotnet
  displayName: Format check
""", "Builds and tests the .NET cart.", setup=DOTNET_SETUP)

BASELINE3_FILES = {
    "/.azure-pipelines/go.yml": GO_YML,
    "/.azure-pipelines/java.yml": JAVA_YML,
    "/.azure-pipelines/dotnet.yml": DOTNET_YML,
}
PROJECT_FILES = [
    "go/go.mod", "go/cart.go", "go/cart_test.go",
    "java/pom.xml", "java/src/main/java/com/example/Cart.java", "java/src/test/java/com/example/CartTest.java",
    "dotnet/Cart/Cart.csproj", "dotnet/Cart/Cart.cs", "dotnet/Cart.Tests/Cart.Tests.csproj", "dotnet/Cart.Tests/CartTests.cs",
]

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
add("test-failure", "click-go", [rep("go/cart.go", "return amount - amount*percent/100", "return amount - percent")],
    "Simplify the discount", "go-test-assertion")
add("code-error", "click-go", [rep("go/cart.go", "sum += it.Price * it.Qty", "sum += it.Price * it.Quantity")],
    "Use the full field name", "go-undefined-field")
add("code-error", "click-go", [rep("go/cart.go", "\t\tsum += it.Price * it.Qty\n\t}\n", "\t\tsum += it.Price * it.Qty\n")],
    "Tidy the total loop", "go-syntax-error")
add("lint", "click-go", [rep("go/cart.go", "package cart\n", "package cart\n\nimport \"fmt\"\n"),
                         app("go/cart.go", "\nfunc describe(i Item) string {\n\treturn fmt.Sprintf(\"%d\", \"price\")\n}\n")],
    "Add a description helper", "go-vet-printf")
add("lint", "click-go", [rep("go/cart.go", "\tsum := 0\n", "    sum := 0\n")],
    "Reformat the total", "gofmt")
add("dependency", "click-go", [rep("go/cart.go", "package cart\n", "package cart\n\nimport _ \"github.com/acme-internal/pricing\"\n")],
    "Use the shared pricing module", "go-missing-module")
add("network", "click-go", [rep(GOY, "- script: go build ./...", "- script: go install golang.org/x/tools/cmd/goimports@latest\n        displayName: Install tools\n        env:\n          GOPROXY: https://proxy.corp.invalid\n      - script: go build ./...")],
    "Install goimports through the build proxy", "go-unreachable-proxy")
add("config", "click-go", [rep("go/go.mod", "go 1.22", "go 1.2x")],
    "Relax the Go version", "go-invalid-version")
add("timeout", "click-go", [new("go/slow_test.go", "package cart\n\nimport (\n\t\"testing\"\n\t\"time\"\n)\n\nfunc TestWaitsForFeed(t *testing.T) {\n\ttime.Sleep(time.Hour)\n}\n")],
    "Add a test for the feed", "go-blocked-test")

# ------------------------------------------------------------------ Java (Maven)
add("test-failure", "click-java", [rep("java/src/main/java/com/example/Cart.java", "return amount - amount * percent / 100;", "return amount - percent;")],
    "Simplify the discount", "junit-assertion")
add("code-error", "click-java", [rep("java/src/main/java/com/example/Cart.java", "item.price() * item.qty()", "item.price() * item.quantity()")],
    "Use the full accessor name", "java-cannot-find-symbol")
add("code-error", "click-java", [rep("java/src/main/java/com/example/Cart.java", "int sum = 0;", "int sum = 0")],
    "Tidy the total", "java-missing-semicolon")
add("dependency", "click-java", [rep("java/pom.xml", "<version>5.10.2</version>", "<version>5.99.0</version>")],
    "Move to the newest JUnit", "maven-missing-version")
add("dependency", "click-java", [rep("java/pom.xml", "  </dependencies>", "    <dependency>\n      <groupId>com.acme</groupId>\n      <artifactId>pricing</artifactId>\n      <version>1.0</version>\n    </dependency>\n  </dependencies>")],
    "Use the shared pricing library", "maven-missing-artifact")
add("network", "click-java", [rep("java/pom.xml", "  <dependencies>", "  <repositories>\n    <repository>\n      <id>corp</id>\n      <url>https://repo.corp.invalid/maven</url>\n    </repository>\n  </repositories>\n\n  <dependencies>"),
                              rep("java/pom.xml", "  </dependencies>", "    <dependency>\n      <groupId>com.acme</groupId>\n      <artifactId>pricing</artifactId>\n      <version>1.0</version>\n    </dependency>\n  </dependencies>")],
    "Resolve the pricing library from the company repository", "maven-unreachable-repo", ambiguous=True,
    note="a missing artifact and an unreachable repository appear together")
add("config", "click-java", [rep("java/pom.xml", "  </dependencies>\n", "")],
    "Tidy the pom", "maven-invalid-pom")
add("test-failure", "click-java", [rep("java/src/test/java/com/example/CartTest.java", "Cart.total(List.of(new Cart.Item(5, 2), new Cart.Item(1, 3)))", "Cart.total(null)")],
    "Test the empty cart", "junit-exception")

# ------------------------------------------------------------------ .NET
add("test-failure", "click-dotnet", [rep("dotnet/Cart/Cart.cs", "=> amount - amount * percent / 100;", "=> amount - percent;")],
    "Simplify the discount", "xunit-assertion")
add("code-error", "click-dotnet", [rep("dotnet/Cart/Cart.cs", "var sum = 0;", "var sum = 0")],
    "Tidy the total", "csharp-missing-semicolon")
add("code-error", "click-dotnet", [rep("dotnet/Cart/Cart.cs", "sum += item.Price * item.Qty;", "sum += item.Price * item.Quantity;")],
    "Use the full property name", "csharp-missing-member")
add("dependency", "click-dotnet", [rep("dotnet/Cart.Tests/Cart.Tests.csproj", 'Include="xunit" Version="2.9.2"', 'Include="xunit" Version="2.99.0"')],
    "Move to the newest xunit", "nuget-missing-version")
add("dependency", "click-dotnet", [rep("dotnet/Cart.Tests/Cart.Tests.csproj", '    <PackageReference Include="xunit" Version="2.9.2" />\n', '    <PackageReference Include="xunit" Version="2.9.2" />\n    <PackageReference Include="Acme.Internal.Kit" Version="1.0.0" />\n')],
    "Use the shared test kit", "nuget-missing-package")
add("network", "click-dotnet", [rep(DNY, "- script: dotnet restore Cart.Tests/Cart.Tests.csproj", "- script: dotnet restore Cart.Tests/Cart.Tests.csproj --source https://nuget.corp.invalid/v3/index.json")],
    "Restore from the company feed", "nuget-unreachable-source")
add("config", "click-dotnet", [rep("dotnet/Cart/Cart.csproj", "</PropertyGroup>", "</PropertyGrou>")],
    "Tidy the project file", "csproj-invalid-xml")
add("lint", "click-dotnet", [rep("dotnet/Cart/Cart.cs", "        var sum = 0;", "      var sum = 0;")],
    "Reindent the total", "dotnet-format")

# ------------------------------------------------------------------ fresh instances on the existing tools
add("lint", "click-web", [rep("web/src/cart.js", "module.exports = {", "function helper() {\n  return discountRate;\n}\n\nmodule.exports = {")],
    "Add a pricing helper", "eslint-undefined-variable")
add("test-failure", "click-web", [rep("web/src/cart.js", "item.price * item.qty", "item.priec * item.qty")],
    "Rename the price field", "jest-nan")
add("code-error", "click-web", [rep("web/src/cart.test.js", 'const { addItem, total, applyDiscount } = require("./cart");', 'import { addItem, total, applyDiscount } from "./cart";')],
    "Use import syntax in the tests", "js-esm-in-commonjs")
add("dependency", "click-container", [rep("Dockerfile", "RUN pip install --no-cache-dir .", 'RUN pip install --no-cache-dir . && python -c "import yaml"')],
    "Check the yaml extra imports", "docker-missing-module")
add("config", "click-container", [rep("Dockerfile", "CMD [", "RUN chmod +x /nonexistent.sh\nCMD [")],
    "Make the entry script executable", "docker-missing-file")
add("permissions", "click-container", [rep("Dockerfile", "RUN pip install --no-cache-dir .", "USER nobody\nRUN pip install --no-cache-dir .")],
    "Run the build as an unprivileged user", "docker-user-permissions")
add("test-failure", "click-tests", [rep("src/click/termui.py", '"Y/n" if default else "y/N"', '"y/N" if default else "Y/n"')],
    "Swap the confirm hints", "py-assertion")
add("type-error", "click-typecheck", [app("src/click/utils.py", "\n\ndef _label(x: int) -> str:\n    return x\n")],
    "Add a label helper", "mypy-return-type")
add("type-error", "click-typecheck", [app("src/click/utils.py", "\n\n_LIMIT: int = 1  # type: ignore\n")],
    "Silence the limit check", "mypy-unused-ignore")
add("lint", "click-lint", [app("src/click/utils.py", "\n\nfrom typing import List\n\n\ndef _names(x: List[str]) -> None:\n    return None\n")],
    "Add a names helper", "ruff-pyupgrade")
add("test-failure", "click-tests", [new("tests/test_fixture_typo.py", "def test_uses_a_fixture_that_does_not_exist(tmpdirr):\n    assert tmpdirr\n")],
    "Add a temp directory test", "pytest-missing-fixture")

# ------------------------------------------------------------------ failures with custom messages (no rule could know them)
add("other", "click-tests", [new("scripts/check_catalog.py", 'import sys\n\nprint("ERROR: catalog checksum mismatch for items.json (expected 9f2c, got 77ab)")\nsys.exit(1)\n'),
                             rep(TESTS, "- script: python -m pytest", "- script: python scripts/check_catalog.py\n        displayName: Check catalog\n      - script: python -m pytest")],
    "Check the catalog before testing", "custom-error-line")
add("other", "click-tests", [new("scripts/reconcile.py", 'import sys\n\nprint("reconciliation incomplete: 3 of 5 shards disagree")\nsys.exit(2)\n'),
                             rep(TESTS, "- script: python -m pytest", "- script: python scripts/reconcile.py\n        displayName: Reconcile shards\n      - script: python -m pytest")],
    "Reconcile shards before testing", "custom-status-message")
add("other", "click-tests", [rep(TESTS, "- script: python -m pytest", "- script: python -c \"import ctypes; ctypes.string_at(0)\"\n        displayName: Native smoke check\n      - script: python -m pytest")],
    "Add a native smoke check", "native-crash")
