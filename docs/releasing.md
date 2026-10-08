# Release procedure

Use `python scripts/version.py <newversion|major|minor|patch|premajor|preminor|prepatch|prerelease>` to update the Docker version in `pipe.yml`, version references in the README and wrapper contract, then create a version commit and `v<version>` Git tag. The script reads the current version from `pipe.yml` and requires a clean working tree. Treat config, output, or verdict changes that require consumers to change their setup as breaking changes. Before running it:

1. Run the Python suite and a Docker smoke test. Review the reports and SQLite evidence for consistency and token redaction.
2. Run the version script on the intended release commit. It creates the exact version tag locally; push the commit and tag to trigger `.github/workflows/release.yml`, which builds and pushes `daviddkppw/api-mitigation-test:<major>.<minor>.<patch>` and its Docker major tag.
3. Verify the published Docker Hub digest and run the released image against the disposable test target. Move the GitHub `v<major>` Action tag to the latest compatible release commit and push it so `peopleware/api-mitigation-test@v<major>` resolves as documented. Coordinate changes to an existing major tag with maintainers and consumers.

Publishing requires access to the GitHub repository, the Docker Hub namespace, and repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. A local Docker build does not publish an image. Do not claim a release is available until the image and Git tags are visible and verified.

## Deploy and operate

The image and Action are delivery mechanisms; each consuming project owns its target config, CI invocation, secured `API_MITIGATION_BEARER_TOKEN`, and evidence retention. After a release, update consuming projects to the intended Action major tag or Bitbucket pipe image version, run against their isolated API, and capture artifacts even when the test fails. Bitbucket Cloud CI artifacts are short-lived; the consuming project must archive every per-build `evidence.sqlite` durably under its own retention and access rules. See [CI integration](ci-integration.md) and [evidence outputs](evidence.md).
