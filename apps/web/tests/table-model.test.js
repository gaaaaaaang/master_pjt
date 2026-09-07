import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tableView } from '../src/table-model.js';
const rows = [{name:'Dry_Etch 10',value:'12'}, {name:'Dry_Etch 2',value:2}, {name:'CMP',value:null}, {name:'증착',value:''}];
test('Numeric sorting preserves missing values and source order', () => {
  const sorted = tableView(rows, '', {key:'value',direction:'descending'});
  assert.deepEqual(sorted.map(row=>row.value), ['12',2,null,'']);
  assert.equal(rows[0].name,'Dry_Etch 10');
  assert.deepEqual(tableView(rows,'',{key:'value',direction:'ascending'}).map(row=>row.value), [2,'12',null,'']);
});
test('Search is case insensitive and supports Korean and nested values', () => {
  assert.equal(tableView(rows,'dry_etch').length,2);
  assert.equal(tableView(rows,'증착').length,1);
  assert.equal(tableView([{detail:{공정:'식각'}}],'식각').length,1);
  assert.equal(tableView(rows,'없는 값').length,0);
});
test('Text sorting uses numeric-aware collation and does not mutate input', () => {
  const sorted=tableView(rows,'dry',{key:'name',direction:'ascending'});
  assert.equal(sorted[0].name,'Dry_Etch 2'); assert.equal(rows[0].name,'Dry_Etch 10');
});
