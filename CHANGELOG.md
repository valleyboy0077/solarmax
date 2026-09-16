# Changelog

All notable Solarmax releases are documented here.

The project follows [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-09-17

### Added

- React WebUI Overview dashboard screenshot in the repository README.
- Version manifest in `VERSION` and release history documentation.
- Semantic daily net bill chart colours: positive amounts are green and
  negative amounts are red.

### Changed

- Improved billing and TOU presentation in the React WebUI.
- Preserved server-authoritative billing calculations and signed chart values.

### Verification

- Frontend tests: 22 passed.
- TypeScript typecheck, ESLint, and production build passed.
- Docker image rebuilt and the live service verified on port 9117.