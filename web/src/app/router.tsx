import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "../components/shell/app-shell";
import { RouteErrorBoundary } from "./route-error-boundary";
import { BillingRoute, DashboardRoute, InvertersRoute, PlansRoute, SettingsRoute, Sigstor20DailyRoute, Sigstor20HourlyRoute } from "../routes/pages";

export const router = createBrowserRouter([
  { path: "/", element: <AppShell />, errorElement: <RouteErrorBoundary />, children: [
    { index: true, element: <DashboardRoute /> },
    { path: "inverters", element: <InvertersRoute /> },
    { path: "plans", element: <PlansRoute /> },
    { path: "billing", element: <BillingRoute /> },
    { path: "sigstor20-daily", element: <Sigstor20DailyRoute /> },
    { path: "sigstor20-hourly", element: <Sigstor20HourlyRoute /> },
    { path: "settings", element: <SettingsRoute /> },
  ] },
]);
