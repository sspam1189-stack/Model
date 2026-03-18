import fs from 'fs';
const h = JSON.parse(fs.readFileSync('data/history.json','utf8'));
const runs = h.runs || [];
console.log('Last 5 NBA run dates:');
for (const r of runs.slice(-5)) {
  console.log('  ', r.dateDisplay || r.date);
}
console.log('Total runs:', runs.length);
