/**
 * OpenClaw Hook: automatic memory/knowledge migration.
 *
 * Behavior:
 * - Trigger on agent:bootstrap
 * - Skip subagent sessions
 * - Auto-detect workspace root
 * - Sync bundled skill payload into ~/.openclaw/skills
 * - Run scripts/auto_migrate.py in quiet mode
 *   (includes bootstrap/ingest + change-aware self-evolve repair chain)
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

function isBootstrapEvent(event) {
  if (!event || typeof event !== 'object') return false;
  if (event.type !== 'agent' || event.action !== 'bootstrap') return false;
  const sessionKey = String(event.sessionKey || '');
  if (sessionKey.includes(':subagent:')) return false;
  return true;
}

function hasWorkspaceMarkers(abs) {
  const markers = [
    'AGENTS.md',
    'MEMORY.md',
    '.adaptr-v1',
    '.openclaw',
  ];
  return markers.some((name) => fs.existsSync(path.join(abs, name)));
}

/**
 * 判断一个 memory 目录是否仅含 runtime 数据（main.sqlite），而没有用户文件。
 * 用来防止把 ~/.openclaw/workspace/memory（仅有 main.sqlite）误判为用户工作区。
 */
function isRuntimeOnlyMemory(memDir) {
  if (!fs.existsSync(memDir)) return false;
  try {
    const entries = fs.readdirSync(memDir);
    const names = new Set(entries);
    if (names.has('.adaptr-v1')) return false;
    const meaningfulFiles = entries.filter(
      (n) => n !== 'main.sqlite' && !n.startsWith('.')
    );
    return names.has('main.sqlite') && meaningfulFiles.length === 0;
  } catch {
    return false;
  }
}

function looksLikeWorkspaceRoot(p, options = {}) {
  if (!p) return false;
  const {
    allowDirectPair = true,
    requireDirectPairMarkers = false,
  } = options;
  const abs = path.resolve(String(p));
  const hasWorkspaceSubdir =
    fs.existsSync(path.join(abs, 'workspace', 'memory')) ||
    fs.existsSync(path.join(abs, 'workspace', 'knowledge'));
  const hasDirectMemory = fs.existsSync(path.join(abs, 'memory'));
  const hasDirectKnowledge = fs.existsSync(path.join(abs, 'knowledge'));
  const isWorkspaceDir = path.basename(abs) === 'workspace';

  if (hasWorkspaceSubdir) {
    return true;
  }
  if (isWorkspaceDir && (hasDirectMemory || hasDirectKnowledge)) {
    return true;
  }
  if (!allowDirectPair) {
    return false;
  }
  if (!(hasDirectMemory && hasDirectKnowledge)) {
    return false;
  }
  if (requireDirectPairMarkers && !hasWorkspaceMarkers(abs)) {
    return false;
  }
  return true;
}

function findWorkspaceRoot(event) {
  // Priority 1: OpenClaw bootstrap 标准字段 context.workspaceDir（最可靠）
  const workspaceDir = event?.context?.workspaceDir;
  if (workspaceDir && typeof workspaceDir === 'string' && fs.existsSync(workspaceDir)) {
    return path.resolve(workspaceDir);
  }

  // Priority 2: 其他显式 workspace 路径字段
  const explicitCandidates = [
    event?.context?.workspacePath,
    event?.context?.workspace?.path,
    event?.workspacePath,
    process.env.OPENCLAW_WORKSPACE,
  ].filter(Boolean);

  for (const c of explicitCandidates) {
    if (looksLikeWorkspaceRoot(c, { allowDirectPair: true })) {
      return path.resolve(String(c));
    }
  }

  // Priority 3: 启发式发现（cwd / PWD），要求含 workspace 标记
  const heuristicCandidates = [
    event?.cwd,
    process.env.PWD,
    process.cwd(),
  ].filter(Boolean);

  for (const c of heuristicCandidates) {
    if (looksLikeWorkspaceRoot(c, { allowDirectPair: true, requireDirectPairMarkers: true })) {
      return path.resolve(String(c));
    }
  }

  // Priority 4: ~/.openclaw/workspace（标准 OpenClaw 安装布局兜底）
  // 注意：仅在 workspace 目录不是 runtime-only memory 时才接受。
  const home = process.env.HOME || '';
  if (home) {
    const ocWorkspace = path.join(home, '.openclaw', 'workspace');
    if (looksLikeWorkspaceRoot(ocWorkspace, { allowDirectPair: true })) {
      const memDir = path.join(ocWorkspace, 'memory');
      if (!isRuntimeOnlyMemory(memDir)) {
        return path.resolve(ocWorkspace);
      }
    }
  }
  return null;
}

function parseSummaryJson(raw) {
  const text = String(raw || '').trim();
  if (!text) return null;

  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object') return parsed;
  } catch (_) {
    // ignore
  }

  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (!(line.startsWith('{') && line.endsWith('}'))) {
      continue;
    }
    try {
      const parsed = JSON.parse(line);
      if (parsed && typeof parsed === 'object') return parsed;
    } catch (_) {
      // ignore and continue
    }
  }
  return null;
}

function truncateDetail(text, maxChars = 420) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  if (raw.length <= maxChars) return raw;
  return `${raw.slice(0, maxChars)}...(truncated)`;
}

function runAutoMigrate(workspaceRoot) {
  const scriptCandidates = [
    // Hook self-contained package (recommended)
    path.join(__dirname, 'scripts', 'auto_migrate.py'),
    // Skill installed under ~/.openclaw/skills/<skill-name>/...
    path.join(
      __dirname,
      '..',
      '..',
      'skills',
      'openclaw-memory-knowledge',
      'scripts',
      'auto_migrate.py'
    ),
    // Development layout fallback
    path.join(__dirname, '..', '..', 'scripts', 'auto_migrate.py'),
  ];

  const scriptPath = scriptCandidates.find((p) => fs.existsSync(p));
  if (!scriptPath) {
    return {
      ok: false,
      message: `auto_migrate.py not found in candidates: ${scriptCandidates.join(', ')}`,
    };
  }

  const py = process.env.PYTHON || 'python3';
  const args = [scriptPath, '--quiet', '--emit-summary-json'];
  if (workspaceRoot) {
    args.push('--workspace-root', workspaceRoot);
  }
  const result = spawnSync(
    py,
    args,
    {
      encoding: 'utf8',
      timeout: 180000,
      maxBuffer: 1024 * 1024,
      cwd: path.dirname(scriptPath),
    }
  );

  if (result.error) {
    return {
      ok: false,
      message: String(result.error.message || result.error),
      summary: null,
    };
  }

  const summary = parseSummaryJson(result.stdout) || parseSummaryJson(result.stderr);

  if (result.status !== 0) {
    const stderr = (result.stderr || '').trim();
    const stdout = (result.stdout || '').trim();
    const detail = stderr || stdout || `exit code ${result.status}`;
    return {
      ok: false,
      message: truncateDetail(detail),
      summary,
    };
  }

  if (summary && summary.ok === false) {
    const detail = summary.error_detail || summary.error || 'migration finished with warning';
    return {
      ok: false,
      message: truncateDetail(detail),
      summary,
    };
  }

  return {
    ok: true,
    message: 'memory/knowledge migration executed',
    summary,
  };
}

function getOpenclawHome() {
  if (process.env.OPENCLAW_HOME) {
    return path.resolve(String(process.env.OPENCLAW_HOME));
  }
  if (process.env.HOME) {
    return path.join(process.env.HOME, '.openclaw');
  }
  return '';
}

function walkFiles(rootDir, maxDepth = 10) {
  const files = [];
  const visited = new Set();
  const stack = [{ dir: rootDir, depth: 0 }];
  while (stack.length > 0) {
    const { dir: current, depth } = stack.pop();
    if (depth > maxDepth) continue;
    let realPath;
    try {
      realPath = fs.realpathSync(current);
    } catch {
      continue;
    }
    if (visited.has(realPath)) continue;
    visited.add(realPath);
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
        .sort((a, b) => a.name.localeCompare(b.name));
    } catch {
      continue;
    }
    for (const entry of entries) {
      const abs = path.join(current, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        stack.push({ dir: abs, depth: depth + 1 });
      } else if (entry.isFile()) {
        files.push(abs);
      }
    }
  }
  return files.sort();
}

function fingerprintDirectory(dirPath) {
  const hash = crypto.createHash('sha256');
  const files = walkFiles(dirPath);
  for (const abs of files) {
    const rel = path.relative(dirPath, abs).replace(/\\/g, '/');
    hash.update(rel);
    hash.update('\0');
    hash.update(fs.readFileSync(abs));
    hash.update('\0');
  }
  return hash.digest('hex');
}

function copyDirAtomic(sourceDir, targetDir) {
  const parent = path.dirname(targetDir);
  fs.mkdirSync(parent, { recursive: true });

  const tempDir = `${targetDir}.tmp-${process.pid}-${Date.now()}`;
  fs.rmSync(tempDir, { recursive: true, force: true });
  fs.cpSync(sourceDir, tempDir, { recursive: true });

  fs.rmSync(targetDir, { recursive: true, force: true });
  fs.renameSync(tempDir, targetDir);
}

function syncBundledSkill() {
  const payloadSkillDir = path.join(__dirname, 'skills', 'openclaw-memory-knowledge');
  const payloadSkillMd = path.join(payloadSkillDir, 'SKILL.md');
  if (!fs.existsSync(payloadSkillMd)) {
    return {
      ok: true,
      changed: false,
      skipped: true,
      message: 'bundled skill payload missing (skip sync)',
    };
  }

  const openclawHome = getOpenclawHome();
  if (!openclawHome) {
    return {
      ok: false,
      changed: false,
      message: 'cannot resolve OPENCLAW_HOME',
    };
  }

  const targetSkillDir = path.join(openclawHome, 'skills', 'openclaw-memory-knowledge');
  const markerPath = path.join(targetSkillDir, '.hook_payload.sha256');

  try {
    const payloadHash = fingerprintDirectory(payloadSkillDir);
    if (fs.existsSync(markerPath) && fs.existsSync(path.join(targetSkillDir, 'SKILL.md'))) {
      const installedHash = fs.readFileSync(markerPath, 'utf8').trim();
      if (installedHash === payloadHash) {
        return {
          ok: true,
          changed: false,
          message: `skill already synced: ${targetSkillDir}`,
        };
      }
    }

    copyDirAtomic(payloadSkillDir, targetSkillDir);
    fs.writeFileSync(markerPath, `${payloadHash}\n`, 'utf8');
    return {
      ok: true,
      changed: true,
      message: `skill synced to: ${targetSkillDir}`,
    };
  } catch (err) {
    return {
      ok: false,
      changed: false,
      message: String(err && err.message ? err.message : err),
    };
  }
}

function injectStatus(event, title, body) {
  if (!event?.context || !Array.isArray(event.context.bootstrapFiles)) {
    return;
  }
  event.context.bootstrapFiles.push({
    path: 'MEMORY_KNOWLEDGE_AUTO_MIGRATE.md',
    content: `## ${title}\n\n${body}`,
    virtual: true,
  });
}

function formatMigrationSummary(summary, fallbackWorkspace) {
  if (!summary || typeof summary !== 'object') return [];

  const lines = [];
  const workspace = summary.workspace_root || fallbackWorkspace || '(unknown)';
  lines.push(`Resolved workspace: ${workspace}`);

  if (summary.target_root) {
    lines.push(`Target root: ${summary.target_root}`);
  }
  if (summary.run_mode) {
    lines.push(`Run mode: ${summary.run_mode}`);
  }
  if (typeof summary.changed === 'boolean') {
    lines.push(`Meaningful changes: ${summary.changed ? 'yes' : 'no'}`);
  }
  if (summary.migrate_report_path) {
    lines.push(`Migrate report: ${summary.migrate_report_path}`);
  }
  if (summary.evolve_report_path) {
    lines.push(`Self-evolve report: ${summary.evolve_report_path}`);
  }
  if (summary.error) {
    lines.push(`Error code: ${summary.error}`);
  }
  if (summary.error_detail) {
    lines.push(`Error detail: ${truncateDetail(summary.error_detail, 500)}`);
  }
  if (Array.isArray(summary.hints) && summary.hints.length > 0) {
    lines.push(`Hints: ${summary.hints.map((v) => String(v)).join(' | ')}`);
  }
  return lines;
}

const handler = async (event) => {
  if (!isBootstrapEvent(event)) {
    return;
  }

  const workspaceRoot = findWorkspaceRoot(event);
  const skillSyncResult = syncBundledSkill();

  let migrateResult;
  if (!workspaceRoot) {
    migrateResult = {
      ok: false,
      message: 'workspace root not detected from bootstrap event (safe skip)',
      summary: {
        ok: false,
        error: 'workspace_not_detected',
        hints: [
          '确认 OpenClaw bootstrap 事件携带 workspaceDir/workspacePath，或设置 OPENCLAW_WORKSPACE 指向 workspace 目录（如 ~/.openclaw/workspace）。',
          '为避免误写入非 OpenClaw 目录，本次已安全跳过自动迁移。',
        ],
      },
    };
  } else {
    migrateResult = runAutoMigrate(workspaceRoot);
  }
  const bodyLines = [
    `Workspace (event): ${workspaceRoot || '(auto-detect failed)'}`,
    `Skill sync: ${skillSyncResult.ok ? 'ok' : 'failed'} - ${skillSyncResult.message}`,
    `Migration: ${migrateResult.ok ? 'ok' : 'failed'} - ${migrateResult.message}`,
  ];
  bodyLines.push(...formatMigrationSummary(migrateResult.summary, workspaceRoot));
  const body = bodyLines.join('\n');

  if (migrateResult.ok && skillSyncResult.ok) {
    injectStatus(
      event,
      'Memory Migration Done',
      body
    );
  } else {
    injectStatus(
      event,
      'Memory Migration Warning',
      body
    );
  }
};

module.exports = handler;
module.exports.default = handler;
module.exports.__test = { findWorkspaceRoot, looksLikeWorkspaceRoot, isRuntimeOnlyMemory };
