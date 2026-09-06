# WebUI rollout recovery review

All remediation and release findings were handled on `feature/webui-rollout-recovery`.

1. **Tablet navigation accessibility — FIXED**
   - Navigation links now carry explicit `aria-label` and `title` values while retaining visible labels on wide layouts.
   - Evidence: frontend lint/typecheck pass; production Playwright + axe smoke passes all five routes.

2. **API response caching — FIXED**
   - Middleware applies `Cache-Control: no-store` to every `/api/` response, including handled and unhandled errors and mutations.
   - Evidence: regression test covers state, bill, and mutation responses; live container checks returned `cache-control: no-store`.

3. **Generated OpenAPI frontend types — FIXED**
   - API client, dashboard adapter, poll hook, and mutation result use types imported from `web/src/api/generated.ts`; duplicate dashboard response shapes were removed.
   - Evidence: frontend typecheck and OpenAPI drift check pass.

4. **Docker lint and API-drift verification — FIXED**
   - The builder runs typecheck, ESLint, unit tests, generated OpenAPI comparison, and production build. The comparison is Git-independent inside Docker and `pipefail` prevents generator failures being masked.
   - Evidence: Docker builder completed all checks; local `npm run api:check` passes.

5. **Production React end-to-end smoke test — FIXED**
   - Playwright starts a React-mode backend, stages the freshly built ignored assets, and checks all five routes with axe.
   - Evidence: `npm run build && npm run e2e`: 8 passed.

6. **Python-only runtime image — FIXED**
   - Runtime stage contains only Python dependencies, application files, templates, legacy static assets, and copied production WebUI assets; build-only Node/Python tooling is confined to the builder.
   - Evidence: no-cache image runs unprivileged as `solarmax` UID 10001; Node, GCC, and curl are absent; exactly one current JS and CSS bundle plus the React entrypoint are present.

7. **Complete route parity — FIXED**
   - Billing includes a confirmed close-day action using the existing server-authoritative endpoint; TOU supports load errors, day-end `24:00`, and confirmed row removal.
   - Evidence: parity matrix, production e2e, Python contract suite, and final read-only review.

8. **Strict adapter-kind validation — FIXED**
   - `InverterProfile.adapter_kind` is constrained to the registered adapter literal and unknown create/update values are rejected without changing persisted data.
   - Evidence: `test_inverter_adapter_kind_writes_reject_unknown_values_and_preserve_sigenstor` passes.

9. **Recommendation OpenAPI response model — FIXED**
   - Recommendation endpoint declares the union of `WeatherRecommendationResponse` and negotiated `MutationSuccessResponse`; error responses remain explicitly modelled.
   - Evidence: OpenAPI contract test checks the generated `anyOf` schema; API generation/drift check passes.

10. **Deprecated legacy bill fields — FIXED**
   - Retained `lines` and `rollups` fields are explicitly marked deprecated in the Pydantic response model and generated OpenAPI schema.
   - Evidence: OpenAPI contract test checks both `deprecated: true` markers.
