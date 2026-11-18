import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_workspace_sync_keeps_the_selected_non_exercise_view():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the browser synchronization logic.")

    script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("app/static/js/app.js", "utf8");
const elements = new Proxy({}, {
  get: (target, key) => target[key] || (target[key] = {
    classList: { add() {}, remove() {}, toggle() {} },
    style: {},
  }),
});
const document = {
  querySelectorAll: () => [],
  getElementById: id => id === "logout-button" ? null : elements[id],
  addEventListener() {},
  querySelector: () => ({ content: "" }),
  documentElement: { dataset: {} },
  activeElement: null,
};
const context = {
  document,
  window: { OpenStudyI18n: null, scrollTo() {} },
  console,
  setInterval: () => 1,
  clearInterval() {},
  Headers,
  fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
};
vm.createContext(context);
vm.runInContext(source + `
  renderQuiz = () => {};
  startSharedWorkspacePolling = () => {};
  toast = () => {};
  setView = view => { state.activeView = view; };
  api = async () => ({
    id: 2,
    status: "active",
    exercise_ids: [2],
    exercises: [{ id: 2, kind: "true_false", options: [] }],
  });
  state.activeView = "library";
  state.sharedWorkspaceId = 1;
  state.quiz = [{ id: 1 }];
  syncSharedWorkspace().then(() => {
    if (state.activeView !== "library") {
      throw new Error("Workspace sync changed the selected view to " + state.activeView);
    }
  });
`, context);
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
