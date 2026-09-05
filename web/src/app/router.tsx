import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "../components/shell/app-shell";
import { RouteErrorBoundary } from "./route-error-boundary";
import { BillingRoute, DashboardRoute, InvertersRoute, PlansRoute, SettingsRoute } from "../routes/placeholders";

export const router = createBrowserRouter([
  { path: "/", element: <AppShell />, errorElement: <RouteErrorBoundary />, children: [
    { index: true, element: <DashboardRoute /> },
    { path: "inverters", element: <InvertersRoute /> },
    { path: "plans", element: <PlansRoute /> },
    { path: "billing", element: <BillingRoute /> },
    { path: "settings", element: <SettingsRoute /> },
  ] },
]);
