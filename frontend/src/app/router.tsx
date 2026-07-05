import { lazy, Suspense } from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { App } from "./App";
import { LoadingBlock } from "../components/ui/Primitives";

const SearchPage = lazy(() => import("../features/search/SearchPage").then((module) => ({ default: module.SearchPage })));
const QualityPage = lazy(() => import("../features/quality/QualityPage").then((module) => ({ default: module.QualityPage })));
const AnalyticsPage = lazy(() => import("../features/analytics/AnalyticsPage").then((module) => ({ default: module.AnalyticsPage })));
const GraphPage = lazy(() => import("../features/graph/GraphPage").then((module) => ({ default: module.GraphPage })));
const SourcesPage = lazy(() => import("../features/sources/SourcesPage").then((module) => ({ default: module.SourcesPage })));
const ExternalPage = lazy(() => import("../features/external/ExternalPage").then((module) => ({ default: module.ExternalPage })));
const UsersPage = lazy(() => import("../features/admin/UsersPage").then((module) => ({ default: module.UsersPage })));
const page = (node: React.ReactNode) => <Suspense fallback={<LoadingBlock label="Открываем раздел"/>}>{node}</Suspense>;

const router = createBrowserRouter([{ path: "/", element: <App />, children: [
  { index: true, element: page(<SearchPage />) },
  { path: "graph", element: page(<GraphPage />) },
  { path: "sources", element: page(<SourcesPage />) },
  { path: "external", element: page(<ExternalPage />) },
  { path: "quality", element: page(<QualityPage />) },
  { path: "analytics", element: page(<AnalyticsPage />) },
  { path: "users", element: page(<UsersPage />) },
]}]);
export function AppRouter() { return <RouterProvider router={router} />; }
