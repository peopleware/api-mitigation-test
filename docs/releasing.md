# Release procedure

With npm available and the Python virtual environment activated, `npm run version:bump -- patch` is a shortcut for `python scripts/version.py patch`. It accepts the same version arguments.

Record changes in the root `CHANGELOG.md` under `## Unreleased`, using subsections
such as `### Features`, `### Fixes`, and `### Maintenance`. Commit those notes
before running the version script. The script promotes `## Unreleased` to
`## <newversion>` and includes the changelog in the release commit. Alternatively,
prepare the version heading yourself. Missing, empty, or duplicate release
sections stop the release before files are changed. Historical image references
in the changelog are preserved.

The release workflow checks that the pushed tag exactly matches `v<version>` from
`package.json`, then extracts only that version's section from `CHANGELOG.md` as
the GitHub release body. It fails if the section is missing or has no description.

The `version` field in the root `package.json` is the source of truth for the current release version. Use `python scripts/version.py <newversion|major|minor|patch|premajor|preminor|prepatch|prerelease>` to update that field and every explicit semantic-version reference to `ppwcode/api-mitigation-test` in Git-tracked UTF-8 text files outside the changelog, then create a version commit and `v<version>` Git tag. This includes references with older pinned versions, including the image in `pipe.yml`. Placeholders, major-only tags, and tags such as `local` are left unchanged. The script requires a clean working tree and stages `package.json`, `CHANGELOG.md`, and files whose image references changed. GitHub Action major-tag references must be updated separately when changing major versions. Treat config, output, or verdict changes that require consumers to change their setup as breaking changes. Before running it:

1. Review and commit the changelog notes. Run the Python suite and a Docker smoke test. Review the reports and SQLite evidence for consistency and token redaction.
2. Run the version script on the intended release commit. It creates the exact version tag locally; push the commit and tag to trigger `.github/workflows/release.yml`, which builds and pushes `ppwcode/api-mitigation-test:<major>.<minor>.<patch>` and its Docker major tag.
3. Verify the published Docker Hub digest and run the released image against the disposable test target. Move the GitHub `v<major>` Action tag to the latest compatible release commit and push it so `peopleware/api-mitigation-test@v<major>` resolves as documented. Coordinate changes to an existing major tag with maintainers and consumers.

Publishing requires access to the GitHub repository, the `ppwcode/api-mitigation-test` Docker Hub repository, and repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. With a personal access token, `DOCKERHUB_USERNAME` must be the token owner's Docker Hub username, even when publishing to the `ppwcode` organization. Organization membership alone does not grant push access; the account needs repository write permission. After pushing the image, the workflow uploads `README.md` to the Docker Hub overview and converts relative documentation links to GitHub URLs. The overview update additionally requires repository Admin permission and a token with read/write/delete scope. See the [Docker Hub description action requirements](https://github.com/peter-evans/dockerhub-description#action-inputs). Verify that the overview includes the GitHub repository link and working documentation links. A local Docker build does not publish an image. Do not claim a release is available until the image and Git tags are visible and verified.

## Deploy and operate

For the [October 2026 emSign incident](https://discuss.python.org/t/109576), publish
a new patch release from the updated sources and replace existing runner images
in consuming pipelines. The release build pulls the base image and disables
layer reuse to refresh system CA certificates. Updating sources or a local
virtual environment does not remediate already published images or existing
containers; consumers must pull the new release and recreate their containers.

The image and Action are delivery mechanisms; each consuming project owns its target config, CI invocation, secured `API_MITIGATION_BEARER_TOKEN`, and evidence retention. After a release, update consuming projects to the intended Action major tag or Bitbucket pipe image version, run against their isolated API, and capture artifacts even when the test fails. Bitbucket Cloud CI artifacts are short-lived; the consuming project must archive every per-build `evidence.sqlite` durably under its own retention and access rules. See [CI integration](ci-integration.md) and [evidence outputs](evidence.md).
