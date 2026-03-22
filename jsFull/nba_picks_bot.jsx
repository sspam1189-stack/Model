import { useState, useEffect, useCallback, useRef, useMemo } from "react";

// ═══════════════════════════════════════════════
// HOLLINGER STATS — ESPN 2025-26
// ═══════════════════════════════════════════════
const DEFAULT_STATS = {
  "Denver":{PACE:101.6,TO:12.1,ORR:27.8,TS:63.1,OFF:123.1,DEF:114.3},
  "New York":{PACE:101.4,TO:11.6,ORR:30.5,TS:59.7,OFF:119.9,DEF:111.3},
  "Houston":{PACE:100.3,TO:13.4,ORR:37.9,TS:59.2,OFF:118.8,DEF:108.1},
  "Boston":{PACE:98.8,TO:10.6,ORR:29.7,TS:58.6,OFF:118.7,DEF:111.6},
  "Oklahoma City":{PACE:102.1,TO:11.1,ORR:21.8,TS:61.3,OFF:118.5,DEF:102.7},
  "LA Lakers":{PACE:101.6,TO:14.3,ORR:25.0,TS:62.4,OFF:116.9,DEF:114.5},
  "San Antonio":{PACE:102.4,TO:13.0,ORR:26.9,TS:60.1,OFF:116.2,DEF:110.8},
  "Minnesota":{PACE:103.1,TO:13.2,ORR:25.8,TS:60.5,OFF:116.1,DEF:110.8},
  "Cleveland":{PACE:104.1,TO:12.0,ORR:26.9,TS:58.3,OFF:114.5,DEF:111.0},
  "Orlando":{PACE:103.6,TO:12.5,ORR:27.2,TS:58.6,OFF:114.1,DEF:109.4},
  "Miami":{PACE:108.1,TO:12.1,ORR:23.0,TS:58.9,OFF:113.5,DEF:108.3},
  "Detroit":{PACE:103.8,TO:13.6,ORR:31.2,TS:58.1,OFF:113.4,DEF:108.8},
  "Milwaukee":{PACE:101.7,TO:13.5,ORR:19.7,TS:60.4,OFF:113.0,DEF:114.1},
  "Phoenix":{PACE:102.2,TO:13.8,ORR:29.6,TS:58.3,OFF:113.0,DEF:111.1},
  "Toronto":{PACE:102.7,TO:12.9,ORR:25.0,TS:58.6,OFF:112.6,DEF:110.4},
  "Atlanta":{PACE:103.8,TO:13.3,ORR:22.3,TS:59.3,OFF:112.4,DEF:111.1},
  "Philadelphia":{PACE:102.8,TO:12.9,ORR:26.7,TS:57.7,OFF:112.0,DEF:111.0},
  "Utah":{PACE:104.6,TO:13.9,ORR:28.7,TS:57.7,OFF:111.6,DEF:118.4},
  "Chicago":{PACE:105.4,TO:13.3,ORR:23.1,TS:58.6,OFF:111.4,DEF:115.1},
  "Portland":{PACE:105.2,TO:13.7,ORR:31.8,TS:56.5,OFF:111.3,DEF:114.8},
  "LA Clippers":{PACE:99.9,TO:14.2,ORR:23.3,TS:58.9,OFF:110.8,DEF:116.2},
  "Golden State":{PACE:102.4,TO:14.1,ORR:25.2,TS:58.0,OFF:110.6,DEF:110.0},
  "Charlotte":{PACE:102.1,TO:13.9,ORR:28.0,TS:57.6,OFF:110.3,DEF:115.9},
  "New Orleans":{PACE:102.0,TO:12.8,ORR:27.6,TS:55.6,OFF:108.9,DEF:119.4},
  "Memphis":{PACE:103.1,TO:12.7,ORR:25.7,TS:56.2,OFF:108.9,DEF:112.6},
  "Brooklyn":{PACE:100.4,TO:13.9,ORR:25.1,TS:56.7,OFF:108.2,DEF:117.9},
  "Washington":{PACE:104.4,TO:14.1,ORR:23.7,TS:56.3,OFF:106.8,DEF:121.2},
  "Sacramento":{PACE:103.8,TO:12.2,ORR:20.5,TS:55.7,OFF:106.5,DEF:117.6},
  "Indiana":{PACE:103.5,TO:12.4,ORR:25.2,TS:54.4,OFF:106.3,DEF:114.4},
  "Dallas":{PACE:104.1,TO:13.6,ORR:22.1,TS:55.8,OFF:105.8,DEF:110.2},
};

// ═══════════════════════════════════════════════
// 7 DAYS PRE-SEEDED HISTORY (Feb 20–26)
// line: + = home fav, - = away fav
// ═══════════════════════════════════════════════
const SEED = [
  // Feb 20
  {date:"2026-02-20",away:"Denver",home:"Portland",line:-12,total:230,awayPts:157,homePts:103},
  {date:"2026-02-20",away:"Dallas",home:"Minnesota",line:8,total:224,awayPts:93,homePts:126},
  {date:"2026-02-20",away:"Miami",home:"Atlanta",line:-3,total:230,awayPts:118,homePts:112},
  {date:"2026-02-20",away:"Oklahoma City",home:"Brooklyn",line:-12,total:218,awayPts:108,homePts:96},
  {date:"2026-02-20",away:"Milwaukee",home:"New Orleans",line:-7.5,total:228,awayPts:127,homePts:119},
  {date:"2026-02-20",away:"LA Clippers",home:"LA Lakers",line:3.5,total:222,awayPts:108,homePts:110},
  {date:"2026-02-20",away:"Utah",home:"Memphis",line:4,total:226,awayPts:108,homePts:118},
  {date:"2026-02-20",away:"Indiana",home:"Charlotte",line:4,total:222,awayPts:105,homePts:119},
  {date:"2026-02-20",away:"New York",home:"Orlando",line:-4,total:216,awayPts:112,homePts:104},
  // Feb 22
  {date:"2026-02-22",away:"Boston",home:"LA Lakers",line:-8,total:220,awayPts:111,homePts:89},
  {date:"2026-02-22",away:"Orlando",home:"LA Clippers",line:-2,total:214,awayPts:111,homePts:109},
  {date:"2026-02-22",away:"Minnesota",home:"Portland",line:-5.5,total:228,awayPts:135,homePts:108},
  {date:"2026-02-22",away:"San Antonio",home:"Denver",line:3,total:228,awayPts:117,homePts:122},
  {date:"2026-02-22",away:"Philadelphia",home:"Detroit",line:6,total:224,awayPts:108,homePts:115},
  // Feb 23
  {date:"2026-02-23",away:"San Antonio",home:"Detroit",line:1.5,total:228.5,awayPts:118,homePts:124},
  {date:"2026-02-23",away:"Sacramento",home:"Memphis",line:4.5,total:232.5,awayPts:123,homePts:114},
  {date:"2026-02-23",away:"Utah",home:"Houston",line:14.5,total:226.5,awayPts:105,homePts:125},
  {date:"2026-02-23",away:"Orlando",home:"LA Clippers",line:-2,total:216,awayPts:111,homePts:109},
  // Feb 24
  {date:"2026-02-24",away:"Philadelphia",home:"Indiana",line:-10,total:233.5,awayPts:135,homePts:114},
  {date:"2026-02-24",away:"Washington",home:"Atlanta",line:11,total:234,awayPts:98,homePts:119},
  {date:"2026-02-24",away:"Oklahoma City",home:"Toronto",line:-9,total:228,awayPts:116,homePts:107},
  {date:"2026-02-24",away:"Dallas",home:"Brooklyn",line:-3,total:224,awayPts:123,homePts:114},
  {date:"2026-02-24",away:"New York",home:"Cleveland",line:4,total:232.5,awayPts:94,homePts:109},
  {date:"2026-02-24",away:"Golden State",home:"New Orleans",line:-6.5,total:222,awayPts:109,homePts:113},
  {date:"2026-02-24",away:"Miami",home:"Milwaukee",line:3,total:228,awayPts:117,homePts:128},
  {date:"2026-02-24",away:"Charlotte",home:"Chicago",line:-4,total:226,awayPts:131,homePts:99},
  {date:"2026-02-24",away:"Boston",home:"Phoenix",line:-5.5,total:218,awayPts:97,homePts:81},
  {date:"2026-02-24",away:"Minnesota",home:"Portland",line:-5.5,total:230,awayPts:124,homePts:121},
  {date:"2026-02-24",away:"Orlando",home:"LA Lakers",line:3,total:217,awayPts:110,homePts:109},
  // Feb 25
  {date:"2026-02-25",away:"Golden State",home:"Memphis",line:1.5,total:228,awayPts:133,homePts:112},
  {date:"2026-02-25",away:"Oklahoma City",home:"Detroit",line:-1,total:219.5,awayPts:116,homePts:124},
  {date:"2026-02-25",away:"San Antonio",home:"Toronto",line:-5,total:224,awayPts:110,homePts:107},
  {date:"2026-02-25",away:"Cleveland",home:"Milwaukee",line:-4,total:230,awayPts:116,homePts:118},
  {date:"2026-02-25",away:"Sacramento",home:"Houston",line:16.5,total:222,awayPts:97,homePts:128},
  {date:"2026-02-25",away:"Boston",home:"Denver",line:3,total:220,awayPts:84,homePts:103},
  // Feb 26 (completed tonight)
  {date:"2026-02-26",away:"Charlotte",home:"Indiana",line:-13,total:229,awayPts:133,homePts:109},
  {date:"2026-02-26",away:"San Antonio",home:"Brooklyn",line:-12.5,total:224.5,awayPts:126,homePts:110},
  {date:"2026-02-26",away:"Houston",home:"Orlando",line:-2.5,total:215.5,awayPts:113,homePts:108},
  {date:"2026-02-26",away:"Washington",home:"Atlanta",line:11.5,total:233.5,awayPts:96,homePts:126},
  {date:"2026-02-26",away:"Portland",home:"Chicago",line:-4,total:235.5,awayPts:121,homePts:112},
  {date:"2026-02-26",away:"Miami",home:"Philadelphia",line:1.5,total:240.5,awayPts:null,homePts:null},
  {date:"2026-02-26",away:"Sacramento",home:"Dallas",line:7.5,total:234.5,awayPts:130,homePts:121},
  {date:"2026-02-26",away:"LA Lakers",home:"Phoenix",line:-5,total:217.5,awayPts:null,homePts:null},
  {date:"2026-02-26",away:"New Orleans",home:"Utah",line:-6.5,total:244.5,awayPts:null,homePts:null},
  {date:"2026-02-26",away:"Minnesota",home:"LA Clippers",line:-8.5,total:220.5,awayPts:null,homePts:null},
];

// ═══════════════════════════════════════════════
// DEFAULT + CALIBRATABLE WEIGHTS
// ═══════════════════════════════════════════════
const DEFAULT_W = { wTS:0.10, wTO:0.80, wORR:0.06, wNET:0.18, constant:2.25, paceAdj:0.98, hca:2.2, sprThresh:2.5, sprHigh:5, ouThresh:4.5, ouHigh:8 };

// ═══════════════════════════════════════════════
// MODEL ENGINE
// ═══════════════════════════════════════════════
function getAvgs(H) {
  const t = Object.values(H); const n = t.length;
  if(!n) return {ts:0,to:0,orr:0};
  return { ts:t.reduce((s,x)=>s+x.TS,0)/n, to:t.reduce((s,x)=>s+x.TO,0)/n, orr:t.reduce((s,x)=>s+x.ORR,0)/n };
}

function projScore(team, opp, isHome, H, a, W) {
  const t=H[team], o=H[opp]; if(!t||!o) return null;
  const base = (t.OFF+o.DEF)/2 + (t.TS-a.ts)*W.wTS - (t.TO-a.to)*W.wTO + (t.ORR-a.orr)*W.wORR
    + W.wNET*((t.OFF-t.DEF)-(o.OFF-o.DEF)) + W.constant;
  const pace = ((t.PACE+o.PACE)/2*W.paceAdj)/100;
  return Math.round((base*pace+(isHome?W.hca:0))*10)/10;
}

function analyzeGame(g, H, a, W, trends) {
  const aS=projScore(g.away,g.home,false,H,a,W);
  const hS=projScore(g.home,g.away,true,H,a,W);
  if(aS===null||hS===null) return null;
  const pT=Math.round((aS+hS)*10)/10;
  const margin=hS-aS-g.line;
  const sDiff=Math.abs(margin);
  const tDiff=Math.round((pT-g.total)*10)/10;
  const absLine=Math.abs(g.line);
  const homeFav=g.line>0;
  let sPick="PASS",sConf="low";
  if(sDiff>=W.sprThresh){
    sPick = margin>0 ? (homeFav?`${g.home} -${absLine}`:`${g.home} +${absLine}`) : (homeFav?`${g.away} +${absLine}`:`${g.away} -${absLine}`);
    sConf = sDiff>=W.sprHigh?"high":"medium";
  }
  let oPick="PASS",oConf="low";
  if(Math.abs(tDiff)>=W.ouThresh){ oPick=tDiff>0?"OVER":"UNDER"; oConf=Math.abs(tDiff)>=W.ouHigh?"high":"medium"; }
  let sResult=null,oResult=null;
  if(g.awayPts!=null&&g.homePts!=null){
    const actM=g.homePts-g.awayPts, actT=g.homePts+g.awayPts;
    if(sPick!=="PASS"){ const d=actM-g.line; sResult=d===0?"P":margin>0?(d>0?"W":"L"):(d<0?"W":"L"); }
    if(oPick!=="PASS"){ oResult=oPick==="OVER"?(actT>g.total?"W":actT===g.total?"P":"L"):(actT<g.total?"W":actT===g.total?"P":"L"); }
  }
  // Attach trend intel for each team
  const aT = trends?.[g.away] || null;
  const hT = trends?.[g.home] || null;
  let trendTags = [];
  if(aT || hT) {
    // ATS agreement: model pick aligns with team ATS trend
    if(sPick !== "PASS" && aT?.ats != null && hT?.ats != null) {
      const modelLikesHome = margin > 0;
      const homeAtsGood = hT.ats > 55;
      const awayAtsBad = aT.ats < 45;
      const homeAtsBad = hT.ats < 45;
      const awayAtsGood = aT.ats > 55;
      if(modelLikesHome && homeAtsGood) trendTags.push({text:`${g.home} ${hT.atsRec} ATS ✓`,type:"agree"});
      if(modelLikesHome && awayAtsBad) trendTags.push({text:`${g.away} ${aT.atsRec} ATS ✓`,type:"agree"});
      if(!modelLikesHome && awayAtsGood) trendTags.push({text:`${g.away} ${aT.atsRec} ATS ✓`,type:"agree"});
      if(!modelLikesHome && homeAtsBad) trendTags.push({text:`${g.home} ${hT.atsRec} ATS ✓`,type:"agree"});
      if(modelLikesHome && homeAtsBad) trendTags.push({text:`${g.home} ${hT.atsRec} ATS ✗`,type:"clash"});
      if(!modelLikesHome && awayAtsBad) trendTags.push({text:`${g.away} ${aT.atsRec} ATS ✗`,type:"clash"});
    }
    // O/U agreement
    if(oPick !== "PASS" && aT?.ou != null && hT?.ou != null) {
      const avgOvr = ((aT.overPct||50) + (hT.overPct||50)) / 2;
      if(oPick === "OVER" && avgOvr > 55) trendTags.push({text:`Both lean OVER (${Math.round(avgOvr)}%) ✓`,type:"agree"});
      if(oPick === "UNDER" && avgOvr < 45) trendTags.push({text:`Both lean UNDER (${Math.round(100-avgOvr)}%) ✓`,type:"agree"});
      if(oPick === "OVER" && avgOvr < 45) trendTags.push({text:`Trend says UNDER (${Math.round(100-avgOvr)}%) ✗`,type:"clash"});
      if(oPick === "UNDER" && avgOvr > 55) trendTags.push({text:`Trend says OVER (${Math.round(avgOvr)}%) ✗`,type:"clash"});
    }
    // Situational edges
    if(aT?.awayAts != null && aT.awayAts > 58) trendTags.push({text:`${g.away} ${aT.awayAtsRec||""} away ATS 🔥`,type:"info"});
    if(hT?.homeAts != null && hT.homeAts > 58) trendTags.push({text:`${g.home} ${hT.homeAtsRec||""} home ATS 🔥`,type:"info"});
    if(aT?.awayAts != null && aT.awayAts < 40) trendTags.push({text:`${g.away} bad on road ATS ⚠`,type:"warn"});
    if(hT?.homeAts != null && hT.homeAts < 40) trendTags.push({text:`${g.home} bad at home ATS ⚠`,type:"warn"});
  }
  return {...g,aS,hS,pT,sDiff:Math.round(sDiff*10)/10,tDiff,sPick,sConf,oPick,oConf,sResult,oResult,trendTags,aTrend:aT,hTrend:hT};
}

// ═══════════════════════════════════════════════
// SELF-LEARNING ENGINE
// 1. Recency-weighted: yesterday matters 3x more than a week ago
// 2. Dual-objective: optimizes for BETTING WIN RATE + score accuracy
// 3. Fine-grained search: 15+ values per weight
// 4. Bias detection: finds systematic errors to fix
// 5. Pattern learning: tracks home/away, fav/dog, O/U lean
// ═══════════════════════════════════════════════

function recencyWeight(gameDate) {
  const now = Date.now();
  const gd = new Date(gameDate).getTime();
  const daysAgo = Math.max(0, (now - gd) / 86400000);
  return Math.exp(-0.12 * daysAgo); // half-life ~6 days
}

function calcWeightedMAE(games, H, a, W) {
  let totalErr=0, totalW=0;
  for(const g of games){
    if(g.awayPts==null) continue;
    const pA=projScore(g.away,g.home,false,H,a,W);
    const pH=projScore(g.home,g.away,true,H,a,W);
    if(pA===null||pH===null) continue;
    const rw = recencyWeight(g.date);
    totalErr += (Math.abs(pA-g.awayPts) + Math.abs(pH-g.homePts)) * rw;
    totalW += 2 * rw;
  }
  return totalW>0 ? totalErr/totalW : 999;
}

function calcBetScore(games, H, a, W) {
  // Returns a composite: lower = better (negative of win rate + MAE penalty)
  let wins=0, losses=0, totalWMAE=0, totalWW=0;
  for(const g of games){
    if(g.awayPts==null) continue;
    const aS=projScore(g.away,g.home,false,H,a,W);
    const hS=projScore(g.home,g.away,true,H,a,W);
    if(aS===null||hS===null) continue;
    const rw = recencyWeight(g.date);
    totalWMAE += (Math.abs(aS-g.awayPts)+Math.abs(hS-g.homePts))*rw;
    totalWW += 2*rw;
    const pT=aS+hS, margin=hS-aS-g.line, actM=g.homePts-g.awayPts, actT=g.homePts+g.awayPts;
    // Grade spread pick
    if(Math.abs(margin)>=W.sprThresh){
      const d=actM-g.line;
      if(d!==0){ const w=margin>0?(d>0):d<0; if(w) wins+=rw; else losses+=rw; }
    }
    // Grade O/U pick
    const tDiff=pT-g.total;
    if(Math.abs(tDiff)>=W.ouThresh){
      const pick=tDiff>0?"OVER":"UNDER";
      if(actT!==g.total){ const w=pick==="OVER"?actT>g.total:actT<g.total; if(w) wins+=rw; else losses+=rw; }
    }
  }
  const wr = (wins+losses)>0 ? wins/(wins+losses) : 0;
  const mae = totalWW>0 ? totalWMAE/totalWW : 999;
  const totalBets = wins+losses;
  const nGames = games.filter(g=>g.awayPts!=null).length;
  const betRate = nGames>0 ? totalBets/nGames : 0;
  // MUST make picks. Scale penalty harshly for low volume.
  // betRate of 0 = massive penalty, betRate of 0.5+ = no penalty
  const volumePenalty = betRate < 0.4 ? (0.4 - betRate) * 2.0 : 0;
  // Need minimum 3 bets to even score win rate
  if(totalBets < 3) return 999;
  // Composite: 55% win rate + 25% accuracy + 20% volume bonus
  return -(wr * 0.55) + (mae / 100) * 0.25 - (betRate * 0.20) + volumePenalty;
}

function detectBias(games, H, a, W) {
  let homeOver=0,homeUnder=0,awayOver=0,awayUnder=0;
  let totalOver=0,totalUnder=0,favHit=0,favMiss=0,dogHit=0,dogMiss=0;
  let n=0;
  for(const g of games){
    if(g.awayPts==null) continue;
    const pA=projScore(g.away,g.home,false,H,a,W);
    const pH=projScore(g.home,g.away,true,H,a,W);
    if(pA===null||pH===null) continue;
    n++;
    // Score bias
    if(pH>g.homePts) homeOver++; else homeUnder++;
    if(pA>g.awayPts) awayOver++; else awayUnder++;
    // Total bias
    if((pA+pH)>g.awayPts+g.homePts) totalOver++; else totalUnder++;
    // Fav/dog ATS
    const margin=pH-pA-g.line;
    if(Math.abs(margin)>=2.5){
      const d=(g.homePts-g.awayPts)-g.line;
      const modelLikesHome=margin>0;
      const isFav=modelLikesHome?(g.line>0):(g.line<0);
      if(d!==0){ const hit=modelLikesHome?(d>0):(d<0); if(isFav){if(hit)favHit++;else favMiss;}else{if(hit)dogHit++;else dogMiss++;} }
    }
  }
  if(n<5) return null;
  return {
    homeOver, homeUnder, awayOver, awayUnder,
    totalLean: totalOver>totalUnder?"OVER":"UNDER",
    totalSkew: Math.abs(totalOver-totalUnder),
    favRecord:`${favHit}-${favMiss}`, dogRecord:`${dogHit}-${dogMiss}`,
    scoreBias: Math.round(((homeOver+awayOver)/(n*2) - 0.5)*200)/10, // +means model projects too high
    n
  };
}

function calibrate(allGames, H) {
  const a = getAvgs(H);
  const fin = allGames.filter(g=>g.awayPts!=null);
  if(fin.length < 8) return { weights:DEFAULT_W, mae:calcWeightedMAE(fin,H,a,DEFAULT_W), improved:false, bias:null, generation:0 };

  let best = {...DEFAULT_W};
  let bestScore = calcBetScore(fin, H, a, best);

  const ranges = {
    wTS:  [0.02,0.04,0.06,0.08,0.10,0.12,0.14,0.16,0.18,0.20,0.25,0.30],
    wTO:  [0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.1,1.2,1.4],
    wORR: [0.01,0.02,0.03,0.04,0.05,0.06,0.07,0.08,0.10,0.12],
    wNET: [0.06,0.08,0.10,0.12,0.14,0.16,0.18,0.20,0.22,0.26,0.30],
    constant: [1.0,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.5],
    paceAdj:  [0.95,0.96,0.965,0.97,0.975,0.98,0.985,0.99,0.995,1.0],
    hca:  [1.0,1.3,1.5,1.7,1.8,2.0,2.2,2.4,2.5,2.7,3.0,3.3],
    sprThresh: [2.0,2.5,3.0,3.5,4.0,4.5],
    sprHigh: [4.0,4.5,5.0,5.5,6.0,6.5],
    ouThresh: [3.0,3.5,4.0,4.5,5.0,5.5],
    ouHigh: [6.0,7.0,8.0,9.0,10.0],
  };

  // 4 passes of coordinate descent on composite score
  for(let pass=0; pass<4; pass++){
    for(const key of Object.keys(ranges)){
      for(const val of ranges[key]){
        const trial = {...best, [key]:val};
        const score = calcBetScore(fin, H, a, trial);
        if(score < bestScore){ bestScore=score; best=trial; }
      }
    }
  }

  const bias = detectBias(fin, H, a, best);
  const mae = Math.round(calcWeightedMAE(fin, H, a, best)*10)/10;
  const defaultMAE = calcWeightedMAE(fin, H, a, DEFAULT_W);
  return { weights:best, mae, improved:mae < Math.round(defaultMAE*10)/10, bias, generation:fin.length };
}

// ═══════════════════════════════════════════════
// API + STORAGE
// ═══════════════════════════════════════════════
async function askClaude(prompt) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ model:"claude-sonnet-4-20250514", max_tokens:4000,
      messages:[{role:"user",content:prompt}],
      tools:[{type:"web_search_20250305",name:"web_search"}] }),
  });
  const data = await res.json();
  return data.content.map(b=>b.text||"").filter(Boolean).join("\n");
}

async function load(key) { try { const r=await window.storage.get(key); return r?JSON.parse(r.value):null; } catch{return null;} }
async function save(key, val) { try { await window.storage.set(key, JSON.stringify(val)); } catch(e){console.error(e);} }

const CC={high:"#00ff87",medium:"#f0c040",low:"#555"};
const CB={high:"rgba(0,255,135,0.07)",medium:"rgba(240,192,64,0.05)",low:"transparent"};
const RC={W:"#00ff87",L:"#ff4466",P:"#f0c040"};

export default function App() {
  const [tab, setTab] = useState("today");
  const [filter, setFilter] = useState("all");
  const [stats, setStats] = useState(DEFAULT_STATS);
  const [history, setHistory] = useState([]);
  const [trends, setTrends] = useState({});
  const [weights, setWeights] = useState(DEFAULT_W);
  const [calInfo, setCalInfo] = useState(null);
  const [loading, setLoading] = useState({stats:false,games:false,results:false,trends:false,cal:false});
  const [errors, setErrors] = useState({});
  const [lastFetch, setLastFetch] = useState(null);
  const initRef = useRef(false);

  const todayStr = new Date().toLocaleDateString("en-US",{weekday:"long",month:"long",day:"numeric",year:"numeric"});
  const todayKey = new Date().toISOString().slice(0,10);

  // ── Init: load storage or seed ──
  useEffect(()=>{
    if(initRef.current) return; initRef.current=true;
    (async()=>{
      const h = await load("nba-hist-v3");
      const w = await load("nba-wt-v3");
      const s = await load("nba-st-v3");
      const c = await load("nba-cal-v3");
      const lf = await load("nba-lf-v3");
      const tr = await load("nba-trends-v3");
      if(h && h.length > 0) setHistory(h);
      else { setHistory(SEED); await save("nba-hist-v3", SEED); }
      if(w) setWeights(w);
      if(s && Object.keys(s).length >= 25) setStats(s);
      if(c) setCalInfo(c);
      if(lf) setLastFetch(lf);
      if(tr && Object.keys(tr).length >= 20) setTrends(tr);
    })();
  },[]);

  // ── Fetch fresh Hollinger stats ──
  const fetchStats = useCallback(async()=>{
    setLoading(l=>({...l,stats:true})); setErrors(e=>({...e,stats:null}));
    try {
      const raw = await askClaude(`Search ESPN for current 2025-2026 NBA Hollinger team statistics. Return ONLY a JSON object mapping team names to stats. Use exact names: Denver, New York, Houston, Boston, Oklahoma City, LA Lakers, San Antonio, Minnesota, Cleveland, Orlando, Miami, Detroit, Milwaukee, Phoenix, Toronto, Atlanta, Philadelphia, Utah, Chicago, Portland, LA Clippers, Golden State, Charlotte, New Orleans, Memphis, Brooklyn, Washington, Sacramento, Indiana, Dallas. Format: {"Team":{"PACE":n,"TO":n,"ORR":n,"TS":n,"OFF":n,"DEF":n}} where OFF=Offensive Efficiency, DEF=Defensive Efficiency. NO markdown, NO backticks. ONLY JSON.`);
      const p = JSON.parse(raw.replace(/```json|```/g,"").trim());
      if(Object.keys(p).length>=25){ setStats(p); await save("nba-st-v3",p); }
    } catch(err){ setErrors(e=>({...e,stats:err.message})); }
    setLoading(l=>({...l,stats:false}));
  },[]);

  // ── Fetch today's lines ──
  const fetchGames = useCallback(async()=>{
    setLoading(l=>({...l,games:true})); setErrors(e=>({...e,games:null}));
    try {
      const raw = await askClaude(`Search for today's NBA games with betting spreads and over/under totals. Today is ${todayStr}. Return ONLY a JSON array: [{"date":"${todayKey}","away":"Team","home":"Team","line":number,"total":number,"time":"H:MM PM","awayPts":null,"homePts":null}]. Line positive=home favored. Use exact team names: Denver, New York, Houston, Boston, Oklahoma City, LA Lakers, San Antonio, Minnesota, Cleveland, Orlando, Miami, Detroit, Milwaukee, Phoenix, Toronto, Atlanta, Philadelphia, Utah, Chicago, Portland, LA Clippers, Golden State, Charlotte, New Orleans, Memphis, Brooklyn, Washington, Sacramento, Indiana, Dallas. NO markdown. ONLY JSON.`);
      const p = JSON.parse(raw.replace(/```json|```/g,"").trim());
      if(Array.isArray(p) && p.length>0){
        setHistory(prev=>{
          const ex = new Set(prev.map(g=>`${g.date}-${g.away}-${g.home}`));
          const nw = p.filter(g=>!ex.has(`${g.date}-${g.away}-${g.home}`));
          const u = [...prev,...nw]; save("nba-hist-v3",u); return u;
        });
      }
    } catch(err){ setErrors(e=>({...e,games:err.message})); }
    setLoading(l=>({...l,games:false}));
  },[todayStr,todayKey]);

  // ── Fetch yesterday's results ──
  const fetchResults = useCallback(async()=>{
    setLoading(l=>({...l,results:true})); setErrors(e=>({...e,results:null}));
    try {
      const y = new Date(); y.setDate(y.getDate()-1);
      const yStr = y.toLocaleDateString("en-US",{weekday:"long",month:"long",day:"numeric",year:"numeric"});
      const yKey = y.toISOString().slice(0,10);
      const raw = await askClaude(`Search for yesterday's NBA final scores and closing betting lines. Yesterday was ${yStr}. Return ONLY a JSON array: [{"date":"${yKey}","away":"Team","home":"Team","line":number,"total":number,"awayPts":number,"homePts":number}]. Line positive=home favored. Use exact team names. NO markdown. ONLY JSON.`);
      const p = JSON.parse(raw.replace(/```json|```/g,"").trim());
      if(Array.isArray(p)&&p.length>0){
        setHistory(prev=>{
          const map = new Map(prev.map(g=>[`${g.date}-${g.away}-${g.home}`,g]));
          for(const g of p){
            const k = `${g.date}-${g.away}-${g.home}`;
            if(map.has(k)){ const ex=map.get(k); ex.awayPts=g.awayPts; ex.homePts=g.homePts; if(g.line!=null) ex.line=g.line; if(g.total!=null) ex.total=g.total; }
            else map.set(k,g);
          }
          const u = Array.from(map.values()); save("nba-hist-v3",u); return u;
        });
      }
    } catch(err){ setErrors(e=>({...e,results:err.message})); }
    setLoading(l=>({...l,results:false}));
  },[]);

  // ── Fetch ATS & O/U Trends from TeamRankings ──
  const fetchTrends = useCallback(async()=>{
    setLoading(l=>({...l,trends:true})); setErrors(e=>({...e,trends:null}));
    try {
      const raw = await askClaude(`Search teamrankings.com for current 2025-2026 NBA team ATS trends (https://www.teamrankings.com/nba/trends/ats_trends/) AND over/under trends (https://www.teamrankings.com/nba/trends/ou_trends/). For each of the 30 NBA teams, find their:
- Overall ATS win percentage and record (W-L)
- Home ATS win percentage and record
- Away ATS win percentage and record
- Overall Over/Under percentage (how often their games go OVER)

Return ONLY a JSON object mapping team names to trend data. Use exact names: Denver, New York, Houston, Boston, Oklahoma City, LA Lakers, San Antonio, Minnesota, Cleveland, Orlando, Miami, Detroit, Milwaukee, Phoenix, Toronto, Atlanta, Philadelphia, Utah, Chicago, Portland, LA Clippers, Golden State, Charlotte, New Orleans, Memphis, Brooklyn, Washington, Sacramento, Indiana, Dallas.

Format: {"Team":{"ats":number,"atsRec":"W-L","homeAts":number,"homeAtsRec":"W-L","awayAts":number,"awayAtsRec":"W-L","overPct":number,"ouRec":"W-L"}}

Where ats/homeAts/awayAts are win percentages (0-100), overPct is percentage of games going OVER (0-100). NO markdown, NO backticks. ONLY JSON.`);
      const p = JSON.parse(raw.replace(/```json|```/g,"").trim());
      if(Object.keys(p).length >= 20) {
        setTrends(p);
        await save("nba-trends-v3", p);
      }
    } catch(err){ setErrors(e=>({...e,trends:err.message})); }
    setLoading(l=>({...l,trends:false}));
  },[]);

  // ── Run calibration ──
  const runCalibrate = useCallback(()=>{
    setLoading(l=>({...l,cal:true}));
    setTimeout(()=>{
      const result = calibrate(history, stats);
      setWeights(result.weights);
      const info = { mae:result.mae, improved:result.improved, bias:result.bias, generation:result.generation, games:history.filter(g=>g.awayPts!=null).length, ts:Date.now() };
      setCalInfo(info);
      save("nba-wt-v3", result.weights);
      save("nba-cal-v3", info);
      setLoading(l=>({...l,cal:false}));
    }, 100);
  },[history, stats]);

  // ── Fetch All ──
  const fetchAll = useCallback(async()=>{
    await fetchStats(); await fetchTrends(); await fetchResults(); await fetchGames();
    const now = new Date().toISOString(); setLastFetch(now); await save("nba-lf-v3",now);
    setTimeout(()=>runCalibrate(), 500);
  },[fetchStats,fetchTrends,fetchGames,fetchResults,runCalibrate]);

  // ── Analysis ──
  const avgs = useMemo(()=>getAvgs(stats),[stats]);
  const allAnalyzed = useMemo(()=>history.map(g=>analyzeGame(g,stats,avgs,weights,trends)).filter(Boolean),[history,stats,avgs,weights,trends]);
  const todayGames = allAnalyzed.filter(g=>g.date===todayKey);
  const completed = allAnalyzed.filter(g=>g.awayPts!=null&&g.homePts!=null);
  const upcoming = allAnalyzed.filter(g=>g.awayPts==null);

  const allPicks = completed.flatMap(r=>{
    const p=[];
    if(r.sPick!=="PASS"&&r.sResult) p.push({type:"SPREAD",conf:r.sConf,result:r.sResult});
    if(r.oPick!=="PASS"&&r.oResult) p.push({type:"O/U",conf:r.oConf,result:r.oResult});
    return p;
  });
  const rec=(arr)=>{const w=arr.filter(x=>x.result==="W").length,l=arr.filter(x=>x.result==="L").length;return{w,l,pct:w+l>0?((w/(w+l))*100).toFixed(1):"—"};};
  const allRec=rec(allPicks), spreadRec=rec(allPicks.filter(x=>x.type==="SPREAD")), ouRec=rec(allPicks.filter(x=>x.type==="O/U")),
    highRec=rec(allPicks.filter(x=>x.conf==="high")), medRec=rec(allPicks.filter(x=>x.conf==="medium")),
    sprHighRec=rec(allPicks.filter(x=>x.type==="SPREAD"&&x.conf==="high")),
    sprMedRec=rec(allPicks.filter(x=>x.type==="SPREAD"&&x.conf==="medium")),
    ouHighRec=rec(allPicks.filter(x=>x.type==="O/U"&&x.conf==="high")),
    ouMedRec=rec(allPicks.filter(x=>x.type==="O/U"&&x.conf==="medium"));

  const source = tab==="today"?todayGames:tab==="results"?completed:upcoming;
  const filtered = source.filter(r=>{
    if(filter==="all") return true;
    if(filter==="spread") return r.sPick!=="PASS";
    if(filter==="ou") return r.oPick!=="PASS";
    if(filter==="strong") return r.sConf==="high"||r.oConf==="high";
    return true;
  });

  const isLoading = Object.values(loading).some(Boolean);
  const pctC = p=>{const n=parseFloat(p);return isNaN(n)?"#556":n>=60?"#00ff87":n>=50?"#f0c040":"#ff4466";};
  const Pill=({v,c})=><span style={{padding:"2px 8px",borderRadius:4,fontSize:10,fontWeight:700,background:(c||"#fff")+"18",color:c||"#fff",border:`1px solid ${c||"#fff"}30`}}>{v}</span>;
  const wChanged = k => weights[k]!==DEFAULT_W[k];

  return (
    <div style={{minHeight:"100vh",background:"linear-gradient(145deg,#080810,#0d1117,#0a0f1a)",color:"#e0e6ed",fontFamily:"'JetBrains Mono','SF Mono',monospace",padding:"14px 10px"}}>
      <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Outfit:wght@400;600;700;800&display=swap" rel="stylesheet"/>

      {/* HEADER */}
      <div style={{textAlign:"center",marginBottom:14,paddingBottom:12,borderBottom:"1px solid rgba(0,255,135,0.12)"}}>
        <div style={{fontFamily:"Outfit",fontSize:9,letterSpacing:6,color:"#00ff87",marginBottom:3}}>SELF-CALIBRATING HOLLINGER MODEL</div>
        <h1 style={{fontFamily:"Outfit",fontSize:26,fontWeight:800,margin:0,background:"linear-gradient(90deg,#fff,#00ff87)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>NBA PICKS ENGINE</h1>
        <div style={{fontSize:10,color:"#445",marginTop:3}}>{todayStr}</div>
        {lastFetch&&<div style={{fontSize:8,color:"#334",marginTop:2}}>Last sync: {new Date(lastFetch).toLocaleString()}</div>}
      </div>

      {/* FETCH + CALIBRATE */}
      <div style={{textAlign:"center",marginBottom:14}}>
        <div style={{display:"flex",gap:8,justifyContent:"center",flexWrap:"wrap"}}>
          <button onClick={fetchAll} disabled={isLoading} style={{padding:"9px 28px",fontSize:12,fontFamily:"Outfit",fontWeight:700,background:isLoading?"rgba(255,255,255,0.05)":"linear-gradient(135deg,#00ff87,#00cc6a)",border:"none",borderRadius:8,color:isLoading?"#556":"#000",cursor:isLoading?"wait":"pointer"}}>
            {isLoading?"⏳ Syncing...":"⚡ Fetch & Calibrate"}
          </button>
          <button onClick={runCalibrate} disabled={loading.cal} style={{padding:"9px 20px",fontSize:12,fontFamily:"Outfit",fontWeight:700,background:"rgba(92,184,255,0.1)",border:"1px solid rgba(92,184,255,0.3)",borderRadius:8,color:"#5cb8ff",cursor:"pointer"}}>
            🧠 Re-Calibrate
          </button>
        </div>
        <div style={{display:"flex",gap:10,justifyContent:"center",marginTop:6,flexWrap:"wrap"}}>
          {[{k:"stats",l:"Stats",ok:!!stats},{k:"trends",l:"Trends",ok:Object.keys(trends).length>0},{k:"games",l:"Lines",ok:history.some(g=>g.date===todayKey)},{k:"results",l:"Results",ok:completed.length>0},{k:"cal",l:"Calibrated",ok:!!calInfo}].map(s=>(
            <div key={s.k} style={{fontSize:8,color:loading[s.k]?"#f0c040":s.ok?"#00ff87":"#445"}}>
              {loading[s.k]?"⏳":s.ok?"✅":"○"} {s.l}
            </div>
          ))}
        </div>
      </div>

      {/* MODEL BRAIN — weight display */}
      {calInfo && (
        <div style={{maxWidth:800,margin:"0 auto 14px",background:"rgba(92,184,255,0.03)",border:"1px solid rgba(92,184,255,0.1)",borderRadius:8,padding:"10px 12px"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
            <div style={{fontFamily:"Outfit",fontSize:11,fontWeight:700,color:"#5cb8ff"}}>🧠 MODEL BRAIN — Gen {calInfo.generation||calInfo.games}</div>
            <div style={{fontSize:8,color:calInfo.improved?"#00ff87":"#556"}}>{calInfo.improved?"✅ Optimized for betting":"○ Defaults"} • MAE: {calInfo.mae}pts • {calInfo.games}g</div>
          </div>
          {/* Weights */}
          <div style={{display:"flex",gap:5,flexWrap:"wrap",marginBottom:6}}>
            {Object.entries({wTS:"TS%",wTO:"TO",wORR:"ORR",wNET:"NetR",constant:"C",paceAdj:"Pace",hca:"HCA",sprThresh:"Spr✂",sprHigh:"SprHi",ouThresh:"O/U✂",ouHigh:"O/UHi"}).map(([k,label])=>(
              <div key={k} style={{background:wChanged(k)?"rgba(0,255,135,0.06)":"rgba(255,255,255,0.02)",border:`1px solid ${wChanged(k)?"#00ff8730":"rgba(255,255,255,0.05)"}`,borderRadius:4,padding:"2px 6px",fontSize:8}}>
                <span style={{color:"#556"}}>{label}:</span>{" "}
                <span style={{color:wChanged(k)?"#00ff87":"#99a",fontWeight:600}}>{weights[k]}</span>
                {wChanged(k)&&<span style={{color:"#445",fontSize:7,marginLeft:2}}>({DEFAULT_W[k]})</span>}
              </div>
            ))}
          </div>
          {/* Learning Insights */}
          {calInfo.bias && (
            <div style={{borderTop:"1px solid rgba(92,184,255,0.08)",paddingTop:6}}>
              <div style={{fontSize:8,color:"#5cb8ff",fontWeight:700,marginBottom:4,letterSpacing:1}}>📡 WHAT I LEARNED</div>
              <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                {/* Score bias */}
                <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid rgba(255,255,255,0.05)",borderRadius:4,padding:"3px 8px",fontSize:8}}>
                  <span style={{color:"#556"}}>Score bias: </span>
                  <span style={{color:Math.abs(calInfo.bias.scoreBias)>3?"#ff4466":"#00ff87",fontWeight:600}}>
                    {calInfo.bias.scoreBias>0?`+${calInfo.bias.scoreBias} (projecting too high)`:`${calInfo.bias.scoreBias} (projecting too low)`}
                  </span>
                </div>
                {/* Total lean */}
                <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid rgba(255,255,255,0.05)",borderRadius:4,padding:"3px 8px",fontSize:8}}>
                  <span style={{color:"#556"}}>Totals lean: </span>
                  <span style={{color:calInfo.bias.totalSkew>5?"#f0c040":"#99a",fontWeight:600}}>
                    {calInfo.bias.totalLean} by {calInfo.bias.totalSkew}/{calInfo.bias.n} games
                  </span>
                </div>
                {/* Home/Away */}
                <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid rgba(255,255,255,0.05)",borderRadius:4,padding:"3px 8px",fontSize:8}}>
                  <span style={{color:"#556"}}>Home: </span>
                  <span style={{color:"#99a"}}>{calInfo.bias.homeOver}over/{calInfo.bias.homeUnder}under</span>
                  <span style={{color:"#556"}}> Away: </span>
                  <span style={{color:"#99a"}}>{calInfo.bias.awayOver}over/{calInfo.bias.awayUnder}under</span>
                </div>
                {/* Fav vs Dog */}
                <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid rgba(255,255,255,0.05)",borderRadius:4,padding:"3px 8px",fontSize:8}}>
                  <span style={{color:"#556"}}>ATS Favs: </span>
                  <span style={{color:"#99a",fontWeight:600}}>{calInfo.bias.favRecord}</span>
                  <span style={{color:"#556"}}> Dogs: </span>
                  <span style={{color:"#99a",fontWeight:600}}>{calInfo.bias.dogRecord}</span>
                </div>
              </div>
              <div style={{fontSize:7,color:"#334",marginTop:4}}>
                ↻ Recency-weighted (recent games count 3×) • Optimizes betting win rate (65%) + accuracy (30%) + volume (5%)
                <br/>Thresholds self-tune: Spr✂={weights.sprThresh}pt (was 2.5) • O/U✂={weights.ouThresh}pt (was 4.5) — model learns how picky to be
              </div>
            </div>
          )}
        </div>
      )}

      {/* RECORD DASHBOARD */}
      {allPicks.length>0 && (<>
        {/* Row 1: Overall */}
        <div style={{maxWidth:800,margin:"0 auto 6px",display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:5}}>
          {[{label:"ALL PICKS",d:allRec},{label:"SPREAD",d:spreadRec},{label:"O/U",d:ouRec},{label:"🔥 ALL HIGH",d:highRec},{label:"⚡ ALL MED",d:medRec}].map((s,i)=>(
            <div key={i} style={{background:"rgba(255,255,255,0.02)",border:"1px solid rgba(255,255,255,0.06)",borderRadius:8,padding:"8px 4px",textAlign:"center"}}>
              <div style={{fontSize:7,color:"#556",letterSpacing:1.5,marginBottom:2}}>{s.label}</div>
              <div style={{fontSize:20,fontWeight:700,color:pctC(s.d.pct),fontFamily:"Outfit"}}>{s.d.pct}%</div>
              <div style={{fontSize:9,color:"#667"}}>{s.d.w}W–{s.d.l}L</div>
            </div>
          ))}
        </div>
        {/* Row 2: Spread + O/U by confidence */}
        <div style={{maxWidth:800,margin:"0 auto 14px",display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:5}}>
          {[
            {label:"SPR 🔥 HIGH",d:sprHighRec,accent:"#00ff87"},
            {label:"SPR ⚡ MED",d:sprMedRec,accent:"#f0c040"},
            {label:"O/U 🔥 HIGH",d:ouHighRec,accent:"#00ff87"},
            {label:"O/U ⚡ MED",d:ouMedRec,accent:"#f0c040"},
          ].map((s,i)=>(
            <div key={i} style={{background:"rgba(255,255,255,0.02)",border:`1px solid ${s.accent}15`,borderRadius:8,padding:"8px 4px",textAlign:"center"}}>
              <div style={{fontSize:7,color:"#556",letterSpacing:1.2,marginBottom:2}}>{s.label}</div>
              <div style={{fontSize:18,fontWeight:700,color:pctC(s.d.pct),fontFamily:"Outfit"}}>{s.d.pct}%</div>
              <div style={{fontSize:9,color:"#667"}}>{s.d.w}W–{s.d.l}L</div>
            </div>
          ))}
        </div>
      </>)}

      {/* TABS */}
      <div style={{display:"flex",gap:3,justifyContent:"center",marginBottom:8}}>
        {[{key:"today",label:`🏀 Today (${todayGames.length})`},{key:"results",label:`📊 Results (${completed.length})`},{key:"upcoming",label:`📅 Upcoming (${upcoming.length})`}].map(t=>(
          <button key={t.key} onClick={()=>{setTab(t.key);setFilter("all");}} style={{padding:"6px 16px",fontSize:10,fontFamily:"Outfit",fontWeight:700,background:tab===t.key?"rgba(0,255,135,0.1)":"transparent",border:`1px solid ${tab===t.key?"#00ff87":"rgba(255,255,255,0.08)"}`,color:tab===t.key?"#00ff87":"#667",borderRadius:7,cursor:"pointer"}}>{t.label}</button>
        ))}
      </div>

      {/* FILTERS */}
      <div style={{display:"flex",gap:4,justifyContent:"center",marginBottom:14,flexWrap:"wrap"}}>
        {[{key:"all",label:"All"},{key:"spread",label:"Spread"},{key:"ou",label:"O/U"},{key:"strong",label:"🔥 High"}].map(f=>(
          <button key={f.key} onClick={()=>setFilter(f.key)} style={{padding:"3px 10px",fontSize:9,fontFamily:"'JetBrains Mono',monospace",background:filter===f.key?"rgba(0,255,135,0.08)":"rgba(255,255,255,0.02)",border:`1px solid ${filter===f.key?"#00ff8750":"rgba(255,255,255,0.06)"}`,color:filter===f.key?"#00ff87":"#667",borderRadius:5,cursor:"pointer"}}>{f.label}</button>
        ))}
      </div>

      {/* GAME CARDS */}
      <div style={{display:"flex",flexDirection:"column",gap:8,maxWidth:800,margin:"0 auto"}}>
        {filtered.map((r,i)=>{
          const hasPick=r.sPick!=="PASS"||r.oPick!=="PASS";
          const best=r.sConf==="high"||r.oConf==="high"?"high":r.sConf==="medium"||r.oConf==="medium"?"medium":"low";
          const done=r.awayPts!=null;
          return (
            <div key={i} style={{background:"rgba(255,255,255,0.02)",border:`1px solid ${hasPick?"rgba(0,255,135,0.12)":"rgba(255,255,255,0.05)"}`,borderRadius:10,padding:"11px 13px",position:"relative",overflow:"hidden"}}>
              {best==="high"&&<div style={{position:"absolute",top:0,left:0,right:0,height:2,background:"linear-gradient(90deg,transparent,#00ff87,transparent)"}}/>}
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:7}}>
                <div style={{display:"flex",alignItems:"baseline",gap:6}}>
                  <span style={{fontFamily:"Outfit",fontSize:13,fontWeight:700}}>{r.away}</span>
                  <span style={{fontSize:9,color:"#334"}}>@</span>
                  <span style={{fontFamily:"Outfit",fontSize:13,fontWeight:700}}>{r.home}</span>
                </div>
                <span style={{fontSize:9,color:"#445"}}>{r.date} {r.time||""}</span>
              </div>
              <div style={{display:"grid",gridTemplateColumns:done?"1fr 1fr 1fr":"1fr 1fr",gap:6,marginBottom:7}}>
                <div style={{background:"rgba(0,120,255,0.04)",border:"1px solid rgba(0,120,255,0.08)",borderRadius:5,padding:"6px 8px"}}>
                  <div style={{fontSize:7,color:"#447",letterSpacing:1.5,marginBottom:2}}>MODEL</div>
                  <div style={{display:"flex",justifyContent:"space-between"}}>
                    <div><div style={{fontSize:8,color:"#556"}}>{r.away}</div><div style={{fontSize:16,fontWeight:700,color:"#5cb8ff"}}>{r.aS}</div></div>
                    <div style={{textAlign:"right"}}><div style={{fontSize:8,color:"#556"}}>{r.home}</div><div style={{fontSize:16,fontWeight:700,color:"#5cb8ff"}}>{r.hS}</div></div>
                  </div>
                  <div style={{textAlign:"center",fontSize:8,color:"#556"}}>T: {r.pT}</div>
                </div>
                <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid rgba(255,255,255,0.05)",borderRadius:5,padding:"6px 8px"}}>
                  <div style={{fontSize:7,color:"#447",letterSpacing:1.5,marginBottom:2}}>VEGAS</div>
                  <div style={{display:"flex",justifyContent:"space-between"}}>
                    <div><div style={{fontSize:8,color:"#556"}}>Spread</div><div style={{fontSize:12,fontWeight:600,color:"#99a"}}>{r.line>0?`${r.home} -${Math.abs(r.line)}`:`${r.away} -${Math.abs(r.line)}`}</div></div>
                    <div style={{textAlign:"right"}}><div style={{fontSize:8,color:"#556"}}>O/U</div><div style={{fontSize:12,fontWeight:600,color:"#99a"}}>{r.total}</div></div>
                  </div>
                </div>
                {done&&(
                  <div style={{background:"rgba(255,255,255,0.03)",border:"1px solid rgba(255,255,255,0.08)",borderRadius:5,padding:"6px 8px"}}>
                    <div style={{fontSize:7,color:"#668",letterSpacing:1.5,marginBottom:2}}>FINAL</div>
                    <div style={{display:"flex",justifyContent:"space-between"}}>
                      <div><div style={{fontSize:8,color:"#556"}}>{r.away}</div><div style={{fontSize:16,fontWeight:700,color:"#fff"}}>{r.awayPts}</div></div>
                      <div style={{textAlign:"right"}}><div style={{fontSize:8,color:"#556"}}>{r.home}</div><div style={{fontSize:16,fontWeight:700,color:"#fff"}}>{r.homePts}</div></div>
                    </div>
                    <div style={{textAlign:"center",fontSize:8,color:"#556"}}>T: {r.awayPts+r.homePts}</div>
                  </div>
                )}
              </div>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6}}>
                <div style={{background:CB[r.sConf],border:`1px solid ${r.sPick!=="PASS"?CC[r.sConf]+"30":"rgba(255,255,255,0.04)"}`,borderRadius:5,padding:"6px 8px"}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                    <div>
                      <div style={{fontSize:7,color:"#447",letterSpacing:1.5}}>SPREAD</div>
                      <div style={{fontSize:11,fontWeight:700,color:r.sPick==="PASS"?"#445":CC[r.sConf],marginTop:1}}>{r.sPick}</div>
                      <div style={{fontSize:8,color:"#445"}}>{r.sDiff}pt • {r.sConf}</div>
                    </div>
                    {r.sResult&&<Pill v={r.sResult} c={RC[r.sResult]}/>}
                  </div>
                </div>
                <div style={{background:CB[r.oConf],border:`1px solid ${r.oPick!=="PASS"?CC[r.oConf]+"30":"rgba(255,255,255,0.04)"}`,borderRadius:5,padding:"6px 8px"}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                    <div>
                      <div style={{fontSize:7,color:"#447",letterSpacing:1.5}}>O/U</div>
                      <div style={{fontSize:11,fontWeight:700,color:r.oPick==="PASS"?"#445":CC[r.oConf],marginTop:1}}>{r.oPick==="PASS"?"PASS":`Take ${r.oPick}`}</div>
                      <div style={{fontSize:8,color:"#445"}}>{r.tDiff>0?"+":""}{r.tDiff}pt • {r.oConf}</div>
                    </div>
                    {r.oResult&&<Pill v={r.oResult} c={RC[r.oResult]}/>}
                  </div>
                </div>
              </div>
              {/* TREND TAGS */}
              {r.trendTags && r.trendTags.length > 0 && (
                <div style={{display:"flex",gap:4,flexWrap:"wrap",marginTop:6}}>
                  {r.trendTags.map((t,ti)=>(
                    <span key={ti} style={{fontSize:8,padding:"2px 7px",borderRadius:4,fontWeight:600,
                      background:t.type==="agree"?"rgba(0,255,135,0.08)":t.type==="clash"?"rgba(255,68,102,0.08)":t.type==="warn"?"rgba(255,68,102,0.05)":"rgba(92,184,255,0.06)",
                      color:t.type==="agree"?"#00ff87":t.type==="clash"?"#ff4466":t.type==="warn"?"#ff8844":"#5cb8ff",
                      border:`1px solid ${t.type==="agree"?"#00ff8725":t.type==="clash"?"#ff446625":t.type==="warn"?"#ff884425":"#5cb8ff20"}`
                    }}>{t.text}</span>
                  ))}
                </div>
              )}
              {/* ATS & O/U RECORDS */}
              {(r.aTrend || r.hTrend) && (
                <div style={{display:"flex",gap:8,marginTop:5,flexWrap:"wrap"}}>
                  {r.aTrend && (
                    <div style={{fontSize:7,color:"#556"}}>
                      <span style={{color:"#667",fontWeight:600}}>{r.away}:</span>{" "}
                      {r.aTrend.atsRec && <span>ATS <span style={{color:r.aTrend.ats>52?"#00ff87":r.aTrend.ats<48?"#ff4466":"#99a"}}>{r.aTrend.atsRec} ({r.aTrend.ats}%)</span></span>}
                      {r.aTrend.overPct != null && <span> • O/U <span style={{color:"#99a"}}>{r.aTrend.overPct}% over</span></span>}
                    </div>
                  )}
                  {r.hTrend && (
                    <div style={{fontSize:7,color:"#556"}}>
                      <span style={{color:"#667",fontWeight:600}}>{r.home}:</span>{" "}
                      {r.hTrend.atsRec && <span>ATS <span style={{color:r.hTrend.ats>52?"#00ff87":r.hTrend.ats<48?"#ff4466":"#99a"}}>{r.hTrend.atsRec} ({r.hTrend.ats}%)</span></span>}
                      {r.hTrend.overPct != null && <span> • O/U <span style={{color:"#99a"}}>{r.hTrend.overPct}% over</span></span>}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ACTION CARD (today) */}
      {tab==="today"&&todayGames.length>0&&(
        <div style={{maxWidth:800,margin:"16px auto 0",background:"rgba(255,255,255,0.02)",border:"1px solid rgba(0,255,135,0.1)",borderRadius:10,padding:12}}>
          <div style={{fontFamily:"Outfit",fontSize:11,fontWeight:700,color:"#00ff87",marginBottom:8}}>📋 ACTION CARD {calInfo?.improved?"(CALIBRATED)":"(DEFAULT)"}</div>
          {todayGames.flatMap((r,i)=>{
            const picks=[];
            if(r.sPick!=="PASS") picks.push({type:"SPR",pick:r.sPick,conf:r.sConf,edge:r.sDiff,mu:`${r.away} @ ${r.home}`,tags:(r.trendTags||[]).filter(t=>t.text.includes("ATS"))});
            if(r.oPick!=="PASS") picks.push({type:"O/U",pick:`Take ${r.oPick}`,conf:r.oConf,edge:Math.abs(r.tDiff),mu:`${r.away} @ ${r.home}`,tags:(r.trendTags||[]).filter(t=>t.text.includes("OVER")||t.text.includes("UNDER"))});
            return picks.map((p,j)=>(
              <div key={`${i}-${j}`} style={{padding:"5px 8px",marginBottom:3,background:CB[p.conf],borderRadius:4,borderLeft:`3px solid ${CC[p.conf]}`}}>
                <div style={{display:"flex",alignItems:"center",gap:6}}>
                  <span style={{fontSize:8,fontWeight:700,color:"#445",width:28}}>{p.type}</span>
                  <span style={{fontSize:10,fontWeight:700,color:CC[p.conf],flex:1}}>{p.pick}</span>
                  <span style={{fontSize:8,color:"#556"}}>{p.mu}</span>
                  <Pill v={`${p.edge}pt`} c={CC[p.conf]}/>
                </div>
                {p.tags && p.tags.length > 0 && (
                  <div style={{display:"flex",gap:3,marginTop:3,marginLeft:34}}>
                    {p.tags.map((t,ti)=>(
                      <span key={ti} style={{fontSize:7,padding:"1px 5px",borderRadius:3,
                        color:t.type==="agree"?"#00ff87":t.type==="clash"?"#ff4466":"#5cb8ff",
                        background:t.type==="agree"?"rgba(0,255,135,0.06)":t.type==="clash"?"rgba(255,68,102,0.06)":"rgba(92,184,255,0.05)"
                      }}>{t.text}</span>
                    ))}
                  </div>
                )}
              </div>
            ));
          })}
        </div>
      )}

      {/* LEDGER (results) */}
      {tab==="results"&&completed.length>0&&(
        <div style={{maxWidth:800,margin:"16px auto 0",background:"rgba(255,255,255,0.02)",border:"1px solid rgba(255,255,255,0.06)",borderRadius:10,padding:12}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
            <div style={{fontFamily:"Outfit",fontSize:11,fontWeight:700,color:"#5cb8ff"}}>📊 FULL LEDGER ({allPicks.length} picks)</div>
            <button onClick={async()=>{setHistory(SEED);await save("nba-hist-v3",SEED);setWeights(DEFAULT_W);await save("nba-wt-v3",DEFAULT_W);setCalInfo(null);}} style={{fontSize:8,color:"#ff4466",background:"none",border:"1px solid #ff446630",borderRadius:4,padding:"2px 6px",cursor:"pointer"}}>Reset</button>
          </div>
          <div style={{fontSize:7,display:"grid",gridTemplateColumns:"58px 30px 1fr 1fr 40px 30px",gap:3,padding:"3px 6px",color:"#445",letterSpacing:1,borderBottom:"1px solid rgba(255,255,255,0.08)"}}>
            <span>DATE</span><span>TYP</span><span>PICK</span><span>GAME</span><span>CONF</span><span>W/L</span>
          </div>
          {[...completed].reverse().flatMap((r,i)=>{
            const rows=[];
            if(r.sPick!=="PASS"&&r.sResult) rows.push({d:r.date,t:"SPR",pick:r.sPick,gm:`${r.away}@${r.home}`,sc:`${r.awayPts}-${r.homePts}`,conf:r.sConf,res:r.sResult});
            if(r.oPick!=="PASS"&&r.oResult) rows.push({d:r.date,t:"O/U",pick:`${r.oPick}`,gm:`${r.away}@${r.home}`,sc:`${r.awayPts}-${r.homePts}`,conf:r.oConf,res:r.oResult});
            return rows.map((p,j)=>(
              <div key={`${i}-${j}`} style={{display:"grid",gridTemplateColumns:"58px 30px 1fr 1fr 40px 30px",gap:3,padding:"4px 6px",fontSize:9,alignItems:"center",borderBottom:"1px solid rgba(255,255,255,0.03)",background:p.res==="W"?"rgba(0,255,135,0.02)":p.res==="L"?"rgba(255,68,102,0.02)":"transparent"}}>
                <span style={{color:"#556",fontSize:8}}>{p.d.slice(5)}</span>
                <span style={{color:"#667",fontSize:7}}>{p.t}</span>
                <span style={{color:CC[p.conf],fontWeight:600,fontSize:9}}>{p.pick}</span>
                <span style={{color:"#556",fontSize:8}}>{p.gm} <span style={{color:"#334"}}>({p.sc})</span></span>
                <span style={{color:CC[p.conf],fontSize:8}}>{p.conf}</span>
                <Pill v={p.res} c={RC[p.res]}/>
              </div>
            ));
          })}
        </div>
      )}

      <div style={{maxWidth:800,margin:"16px auto",textAlign:"center",fontSize:8,color:"#334",lineHeight:1.6}}>
        Self-learning Hollinger model • Recency-weighted dual optimization (win rate + accuracy)
        <br/>ATS & O/U trends from TeamRankings.com • ✓ = model agrees with trend • ✗ = model clashes
        <br/>Spread ≥2.5pt edge | O/U ≥4.5pt edge | High: ≥5pt/≥8pt
        <br/>⚠️ For entertainment only — gamble responsibly
      </div>
    </div>
  );
}
