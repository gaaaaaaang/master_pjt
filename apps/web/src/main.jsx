if (window.location.pathname === "/trace") {
  import("./legacy.jsx");
} else {
  import("./workspace.jsx");
}
