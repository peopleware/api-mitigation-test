<div align="center">
    <img class="brand-mark" src="report_assets/brand-mark.svg" alt="" aria-hidden="true">
    <h1 style="margin: 0">API Mitigation Test</h1>
</div>


<p align="center">
  <a href="https://github.com/peopleware/api-mitigation-test" target="_blank">
    <img alt="Version" src="https://img.shields.io/badge/github-repo-blue?logo=github" />
  </a>
  <a href="https://hub.docker.com/r/daviddkppw/api-mitigation-test" target="_blank">
    <img src="https://img.shields.io/docker/pulls/daviddkppw/api-mitigation-test?logo=docker" />
  </a>
  <img alt="Version" src="https://img.shields.io/docker/v/daviddkppw/api-mitigation-test?color=yellow" />
  <a href="https://github.com/peopleware/api-mitigation-test?tab=Apache-2.0-1-ov-file" target="_blank">
    <img alt="License: Apache--2.0" src="https://img.shields.io/github/license/peopleware/api-mitigation-test" />
  </a>
</p>

<div align="center">
  <p>This Docker image tests an API against its OpenAPI contract using <a href="https://schemathesis.readthedocs.io/en/latest/">Schemathesis</a> and records coverage with <a href="https://docs.tracecov.sh/">TraceCov</a>. Run it in your delivery pipeline to detect regressions, enforce coverage requirements, and retain evidence of the checks performed for each build.</p>
  <p>Continuous mitigation assurance means repeating those checks as your API changes and keeping their results reviewable. A passing run describes what was tested; it does not prove that the entire system is vulnerability-free.</p>
</div>

## 🚀 Usage
1. **Prepare an isolated API environment with Docker Compose (recommended).** Keep one project-owned definition for the API, dependencies, migrations, and mitigation runner. Use your application's supported DBMS and identity provider, and seed representative data and permissions. Tests can change data, so use disposable state and wait for readiness. See the [recommended Compose workflow](docs/ci-integration.md#recommended-docker-compose-workflow).

2. **Commit a test configuration.** Supply a matching OpenAPI schema, a URL reachable from the runner, the component version, and a stable configuration ID. Decide which operations and coverage thresholds should gate delivery. See the [configuration reference](docs/configuration.md).

3. **Provide suitable credentials.** Obtain an API access token for a test identity whose claims and application permissions match the scenarios you want to exercise. Pass it as `API_MITIGATION_BEARER_TOKEN` through the execution environment. The runner does not acquire or refresh tokens. See [authentication and environment requirements](docs/ci-integration.md#prepare-the-target).

4. **Run the tests in CI.** Test each relevant change before deployment, using the Docker image, GitHub Action, or Bitbucket pipe. Let a failing verdict fail the pipeline. Collect reports and service logs even when testing fails, then clean up disposable resources. See [CI integration examples](docs/ci-integration.md).

5. **Review and retain the evidence.** Investigate failures, fix regressions, and use owned, time-limited exceptions only when justified. Archive each build's `evidence.sqlite` under your project's access and retention rules; temporary CI artifacts are insufficient for durable assurance. See [reports, verdicts, and evidence](docs/evidence.md).


## 🤝 Contributing
Contributions, issues and feature requests are welcome!<br />Feel free to check [issues page](https://github.com/peopleware/api-mitigation-test/issues). You can also take a look at the [contributing guide](https://github.com/peopleware/api-mitigation-test?tab=contributing-ov-file).

## 📝 License
Copyright © 2026 [PeopleWare NV](https://github.com/peopleware).<br />
This project is [Apache--2.0](https://github.com/peopleware/api-mitigation-test?tab=Apache-2.0-1-ov-file) licensed.