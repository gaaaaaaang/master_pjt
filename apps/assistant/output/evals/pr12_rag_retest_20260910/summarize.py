import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path('/Users/a11549/Desktop/skax-git/master_pjt')
OUT = ROOT / 'apps/assistant/output/evals/pr12_rag_retest_20260910'
SRC = Path('/private/tmp/pr12-rag-retest-374e100/apps/assistant')
sys.path[:0] = [str(SRC / 'src'), str(SRC / 'scripts')]
from app.rag.grounding import normalized
from evaluate_rag_search import covered_units

before = json.loads((ROOT / 'apps/assistant/output/evals/adv_integrate_kpi_20260910/ragas_live.json').read_text())
after = json.loads((OUT / 'ragas.json').read_text())
assert after.get('completed') and len(after['results']) == 10
fixtures = json.loads((SRC / 'tests/fixtures/rag_extension_eval.json').read_text())
positive = {c['id'] for c in fixtures if c['expected_markers']}
cases = {c['id']: c for c in fixtures}
keys = ['faithfulness', 'answer_relevancy', 'llm_context_precision_without_reference']
def aggregate(report, ids):
    rows = [r for r in report['results'] if r['id'] in ids]
    output = {'n': len(rows), 'metrics': {}}
    for key in keys:
        values = [r.get('scores', {}).get(key) for r in rows]
        valid = [v for v in values if v is not None]
        output['metrics'][key] = {'mean': statistics.mean(valid) if valid else None, 'valid': len(valid)}
    return output

ids = set(cases)
summary = {'pr': 12, 'commit': '374e1000ea761dc6ea3bbcd98b00191e43f46d8a',
           'baseline_commit': '4ebf83350a706afb158653e0c51d5c43b60f9c9e',
           'comparison': {}, 'cases': [], 'regression': json.loads((OUT / 'regression.json').read_text())}
for name, selected in [('all', ids), ('answerable', positive), ('unanswerable', ids-positive)]:
    summary['comparison'][name] = {'before': aggregate(before, selected), 'after': aggregate(after, selected)}
summary['same_conditions'] = {k: before[k] == after[k] for k in ['corpus_sha256', 'fixture_sha256', 'model', 'ragas_version', 'rag_reranker', 'dense_retrieval_configured']}
for old, new in zip(before['results'], after['results'], strict=True):
    assert old['id'] == new['id']
    expected = set(cases[new['id']]['expected_markers'])
    def coverage(row):
        found = set().union(*(covered_units(e['content'], expected) for e in row.get('evidence', [])))
        return expected <= found if expected else None
    issues = []
    citations = new.get('answer', {}).get('citations', [])
    source = {e['metadata']['chunk_id']: e for e in new.get('evidence', [])}
    for citation in citations:
        evidence = source.get(citation.get('chunk_id'))
        if evidence is None:
            issues.append('missing_source')
        else:
            if not citation.get('quote') or normalized(citation['quote']) not in normalized(evidence['content']):
                issues.append('quote_not_in_source')
            for key in ['source_document', 'page_number']:
                if citation.get(key) != evidence['metadata'].get(key): issues.append('metadata_mismatch_'+key)
    summary['cases'].append({'id': new['id'], 'before_status': old.get('answer', {}).get('status'),
        'after_status': new.get('answer', {}).get('status'), 'before_scores': old.get('scores'),
        'after_scores': new.get('scores'), 'before_evidence_count': len(old.get('evidence', [])),
        'after_evidence_count': len(new.get('evidence', [])), 'before_complete_evidence': coverage(old),
        'after_complete_evidence': coverage(new), 'citations': len(citations), 'citation_issues': issues,
        'generation_attempts': new.get('answer',{}).get('review',{}).get('generation_attempts'),
        'validation_errors': new.get('answer',{}).get('review',{}).get('validation_errors'),
        'metric_errors': new.get('metric_errors'), 'error': new.get('error')})
summary['citation_count'] = sum(r['citations'] for r in summary['cases'])
summary['citation_audit_passed'] = all(not r['citation_issues'] for r in summary['cases'])
summary['mean_generation_seconds'] = statistics.mean(r['seconds'] for r in after['results'])
(OUT/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
