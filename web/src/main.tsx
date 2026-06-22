import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import Home from "./pages/Home";
import NeedsYou from "./pages/NeedsYou";
import ProjectPage from "./pages/Project";
import Resources from "./pages/Resources";
import Settings from "./pages/Settings";
import "./styles.css";

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Home /> },
      { path: "needs-you", element: <NeedsYou /> },
      { path: "p/:id", element: <ProjectPage /> },
      { path: "machines", element: <Resources /> },
      { path: "settings", element: <Settings /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
