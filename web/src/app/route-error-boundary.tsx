import { isRouteErrorResponse, useRouteError } from "react-router-dom";

export function RouteErrorBoundary() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error) ? `${error.status} ${error.statusText}` : "This page could not be loaded.";
  return <main className="mx-auto max-w-2xl p-6" id="main-content"><section className="state-panel" role="alert"><h1>Page unavailable</h1><p>{message}</p><a className="link" href="/">Return to overview</a></section></main>;
}
