// Exercise shared modules through the same interfaces as their application callers.
import React from "react";
import { createRoot } from "react-dom/client";
import { Markdown } from "../src/components/shared";
import { usePoll } from "../src/api";
import "../src/styles.css";

const root = createRoot(document.getElementById("root"));
const requests = [];
let refresh;

function Poll({ name = "first", enabled = true, cacheKey }) {
  const poll = usePoll(() => new Promise((resolve, reject) => {
    requests.push({ name, resolve, reject });
  }), [name], 60_000, { enabled, cacheKey });
  refresh = poll.refresh;
  return <output>{JSON.stringify({ data: poll.data, failed: poll.failed })}</output>;
}

window.harness = {
  markdown(text) {
    root.render(<Markdown text={text} />);
  },
  poll(props = {}) {
    root.render(<Poll {...props} />);
  },
  refresh() {
    return refresh();
  },
  requests() {
    return requests.map(({ name }) => name);
  },
  resolve(index, data) {
    requests[index].resolve(data);
  },
  reject(index, message) {
    requests[index].reject(new Error(message));
  },
  unmount() {
    root.unmount();
  },
};
