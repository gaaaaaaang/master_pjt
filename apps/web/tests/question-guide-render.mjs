// Static component rendering only. This does not replace browser layout QA.
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { GUIDE_FABS, QUESTION_FLOWS, guideQuestion } from '../src/question-guide.js';

const root = fileURLToPath(new URL('../', import.meta.url));
const server = await createServer({ root, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { QuestionGuide } = await server.ssrLoadModule('/src/question-guide.jsx');
  let combinations = 0;
  for (const fab of GUIDE_FABS) for (const flow of QUESTION_FLOWS) {
    const markup = renderToStaticMarkup(React.createElement(QuestionGuide, { initialFab: fab, initialFlowId: flow.id, onSelect() {}, onClose() {} }));
    for (const step of flow.steps) if (!markup.includes(guideQuestion(step, fab))) throw new Error(`Missing prompt ${fab}/${flow.id}`);
    if ((markup.match(/aria-pressed="true"/g) || []).length !== 1) throw new Error('Wrong selected flow');
    combinations++;
  }
  const sourceHash = createHash('sha256');
  for (const file of ['question-guide.js', 'question-guide.jsx', 'ui.jsx', 'workspace.css']) sourceHash.update(readFileSync(root + 'src/' + file));
  const report = { checked_at: new Date().toISOString(), combinations, passed: combinations, source_sha256: sourceHash.digest('hex'), scope: 'Static React rendering. Browser layout, focus and interaction are not covered.' };
  if (process.argv[2]) writeFileSync(process.argv[2], JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally { await server.close(); }
