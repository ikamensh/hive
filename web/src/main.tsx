import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import Projects from "./pages/Projects";
import ProjectPage from "./pages/Project";
import Resources from "./pages/Resources";
import Storage from "./pages/Storage";
import "./styles.css";

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Projects /> },
      { path: "p/:id", element: <ProjectPage /> },
      { path: "resources", element: <Resources /> },
      { path: "storage", element: <Storage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
