const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const handlerModule = require('../hooks/openclaw/handler.js');
const { findWorkspaceRoot, isRuntimeOnlyMemory } = handlerModule.__test;

function withTempHome(run) {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mk-handler-test-'));
  const previousHome = process.env.HOME;
  try {
    process.env.HOME = tmpDir;
    run(tmpDir);
  } finally {
    process.env.HOME = previousHome;
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

function testSkipsRuntimeOnlyHomeWorkspace() {
  withTempHome((home) => {
    const memDir = path.join(home, '.openclaw', 'workspace', 'memory');
    fs.mkdirSync(memDir, { recursive: true });
    fs.writeFileSync(path.join(memDir, 'main.sqlite'), '');

    assert.strictEqual(isRuntimeOnlyMemory(memDir), true);
    assert.strictEqual(findWorkspaceRoot({ type: 'agent', action: 'bootstrap', context: {} }), null);
  });
}

function testUsesWorkspaceDirWhenProvided() {
  withTempHome((home) => {
    const workspace = path.join(home, 'project-workspace');
    fs.mkdirSync(path.join(workspace, 'memory'), { recursive: true });
    fs.writeFileSync(path.join(workspace, 'memory', 'notes.md'), '用户记忆');

    const result = findWorkspaceRoot({
      type: 'agent',
      action: 'bootstrap',
      context: { workspaceDir: workspace },
    });

    assert.strictEqual(result, path.resolve(workspace));
  });
}

function testAcceptsHomeWorkspaceWithUserFiles() {
  withTempHome((home) => {
    const workspace = path.join(home, '.openclaw', 'workspace');
    const memDir = path.join(workspace, 'memory');
    fs.mkdirSync(memDir, { recursive: true });
    fs.writeFileSync(path.join(memDir, 'profile.md'), '用户画像');

    const result = findWorkspaceRoot({
      type: 'agent',
      action: 'bootstrap',
      context: {},
    });

    assert.strictEqual(result, path.resolve(workspace));
  });
}

testSkipsRuntimeOnlyHomeWorkspace();
testUsesWorkspaceDirWhenProvided();
testAcceptsHomeWorkspaceWithUserFiles();
