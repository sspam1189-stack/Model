import fs from 'fs';
const d = JSON.parse(fs.readFileSync('data/kalman_state.json','utf8'));
console.log('Top-level keys:', Object.keys(d));
for (const k of Object.keys(d)) {
  if (typeof d[k] !== 'object' || d[k] === null) {
    console.log(`  ${k}: ${d[k]}`);
  } else if (k === 'W' || k === 'W_var' || k === 'hyper') {
    console.log(`  ${k}:`, JSON.stringify(d[k]));
  } else {
    console.log(`  ${k}: [object with ${Object.keys(d[k]).length} keys]`);
  }
}
