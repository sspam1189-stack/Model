import React, { useState, useMemo, useEffect, useCallback, useRef } from "react";

/* ==========================================================================
   2026 DRAFT WAR ROOM  ·  clean rebuild
   --------------------------------------------------------------------------
   Value now flows from real projections, not ADP:
     projected points  ->  VORP (value over replacement at position)
                       ->  edge = where projections rank him vs where ADP does
   Projections pull live from Sleeper in the browser; if that fails the whole
   tool falls back to ADP-implied value and says so.

   Data layers, top to bottom:
     1. PLAYERS      static: name/pos/team/bye/notes + multi-source ADP (Aug 25)
     2. projections  live from Sleeper: points + targets/carries + TD mix
     3. value engine  VORP, value rank, edge, opportunity, risk, auction $
     4. survival      opponent-need-aware "will he last" simulation
     5. UI            board / auction / league / edges / my team / sync
   ========================================================================== */

const THEME = {
  bg: "#0A100D", panel: "#101A15", panel2: "#16231C", line: "#22332A",
  chalk: "#E9F0EA", muted: "#7C9186", dim: "#4F6459",
  signal: "#F2B441", hot: "#E2694F", cool: "#5AA9D6", good: "#4FBF8B",
};
const POS_COLOR = { QB:"#D96B7E", RB:"#4FBF8B", WR:"#56A8E8", TE:"#E0A33E", K:"#8E9BA8", DST:"#9B7FD4" };
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
const DISPLAY = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
const FLEX_OK = ["RB", "WR", "TE"];
const FLEX_SHARE = { RB: 0.4, WR: 0.5, TE: 0.1 };

/* ---- 1. static player data: name,pos,team,bye,ffcPPR,ffcSD,ffcHalf,sleeper,espn,fpros,note ---- */
const RAW = `
Jahmyr Gibbs,RB,DET,6,1.5,0.7,1.5,1.7,1.0,2.6,
Bijan Robinson,RB,ATL,11,2.1,0.7,2.3,2.8,2.0,4.0,
Puka Nacua,WR,LAR,11,3.1,0.9,3.1,4.2,4.0,3.3,
Ja'Marr Chase,WR,CIN,6,3.8,1.0,3.9,3.4,3.0,1.6,
Jaxon Smith-Njigba,WR,SEA,11,5.4,1.1,5.1,6.1,5.0,4.7,
Amon-Ra St. Brown,WR,DET,6,6.4,1.3,7.9,8.7,8.0,5.5,
Christian McCaffrey,RB,SF,8,6.6,1.6,6.5,5.7,6.0,9.7,
Jonathan Taylor,RB,IND,13,7.5,1.7,6.0,7.0,7.0,11.7,
Drake London,WR,ATL,11,10.0,1.7,12.4,17.3,14.0,12.6,
De'Von Achane,RB,MIA,6,10.4,1.9,11.8,12.7,10.0,21.1,
CeeDee Lamb,WR,DAL,14,10.7,1.9,12.5,9.1,9.0,8.9,
Justin Jefferson,WR,MIN,6,11.7,2.3,14.4,11.7,11.0,9.4,
James Cook III,RB,BUF,7,12.7,2.9,9.3,10.3,12.0,16.6,
Chase Brown,RB,CIN,6,13.3,2.5,15.3,16.4,20.0,18.1,
Rashee Rice,WR,KC,5,14.8,2.2,15.1,27.3,16.0,23.6,
Ashton Jeanty,RB,LV,13,15.1,3.2,14.8,13.9,13.0,27.6,Ankle sprain — has slid out of Rd 1 in many rooms
Derrick Henry,RB,BAL,13,17.6,2.6,9.7,20.8,19.0,37.3,Much cheaper in PPR than half/standard
A.J. Brown,WR,NE,11,18.2,3.2,20.0,18.9,28.0,14.1,
Chris Olave,WR,NO,8,19.5,3.3,23.5,30.7,26.0,18.7,
Saquon Barkley,RB,PHI,10,19.5,3.3,17.6,13.5,18.0,24.3,
George Pickens,WR,DAL,14,20.6,3.0,19.9,24.9,32.0,20.1,
Nico Collins,WR,HOU,8,21.0,2.9,19.2,25.1,25.0,15.8,
Kenneth Walker,RB,KC,5,21.8,4.1,20.9,19.9,21.0,27.0,
Omarion Hampton,RB,LAC,7,23.4,4.3,24.3,15.5,22.0,25.5,
Garrett Wilson,WR,NYJ,13,25.7,2.9,31.5,45.5,27.0,29.9,
Zay Flowers,WR,BAL,13,26.1,3.0,25.7,41.6,34.0,30.2,
Malik Nabers,WR,NYG,8,27.3,3.4,26.8,27.2,29.0,24.6,ACL recovery — trending toward Week 1
Jeremiyah Love,RB,ARI,14,27.7,3.6,29.4,26.7,,41.1,Rookie
Trey McBride,TE,ARI,14,29.0,4.7,37.8,20.2,17.0,21.3,
DeVonta Smith,WR,PHI,10,29.8,3.6,31.6,36.7,35.0,24.0,
Josh Jacobs,RB,GB,11,30.6,3.6,25.1,31.5,30.0,44.8,
Kyren Williams,RB,LAR,11,31.6,4.3,27.4,29.0,39.0,41.5,
Tetairoa McMillan,WR,CAR,5,32.2,3.7,30.9,37.8,33.0,35.2,
Josh Allen,QB,BUF,7,34.2,8.6,33.0,22.3,36.0,25.9,
Emeka Egbuka,WR,TB,10,34.8,4.0,33.5,39.8,43.0,40.3,
Breece Hall,RB,NYJ,13,35.3,4.8,35.0,33.0,23.0,40.1,Nagging injury — Braelon Allen has been rising
Brock Bowers,TE,LV,13,35.4,7.3,43.5,23.0,24.0,18.1,
Javonte Williams,RB,DAL,14,36.5,4.5,33.3,34.9,31.0,44.9,
Tee Higgins,WR,CIN,6,36.8,4.9,36.1,34.3,47.0,36.3,
Cam Skattebo,RB,NYG,8,37.1,5.0,37.0,43.4,41.0,57.0,Back from leg/ankle injury; splitting with Tracy
Ladd McConkey,WR,LAC,7,37.8,4.4,41.1,38.2,45.0,35.9,
Travis Etienne Jr.,RB,NO,8,39.1,4.9,38.6,44.9,38.0,48.2,
Davante Adams,WR,LAR,11,42.0,4.3,37.9,50.0,44.0,49.8,
Jameson Williams,WR,DET,6,44.2,4.4,39.3,58.7,52.0,55.6,
Bucky Irving,RB,TB,10,44.7,4.4,46.5,41.6,42.0,59.0,Full speed after shoulder surgery
D'Andre Swift,RB,CHI,10,45.7,4.4,43.3,55.0,65.0,55.9,
Jaylen Waddle,WR,DEN,10,46.0,5.5,47.8,46.5,48.0,35.9,Popular "mispriced" call all summer
Terry McLaurin,WR,WAS,7,46.9,4.7,44.8,55.1,46.0,46.5,
DJ Moore,WR,BUF,7,48.5,6.3,45.6,57.2,53.0,52.0,
Quinshon Judkins,RB,CLE,11,50.4,5.1,51.8,54.7,40.0,61.7,
Drake Maye,QB,NE,11,51.8,7.8,51.4,48.1,60.0,37.9,
Bhayshul Tuten,RB,JAX,7,52.2,5.4,52.7,62.4,64.0,67.6,
Rome Odunze,WR,CHI,10,52.6,6.2,48.4,65.9,49.0,57.0,
Mike Evans,WR,SF,8,54.3,6.3,54.2,61.0,87.0,55.1,
Lamar Jackson,QB,BAL,13,56.9,6.8,55.6,32.6,58.0,31.9,
Colston Loveland,TE,CHI,10,57.0,9.0,62.2,40.2,50.0,37.9,
Joe Burrow,QB,CIN,6,57.9,7.4,57.1,51.4,81.0,46.9,
Luther Burden III,WR,CHI,10,58.3,7.8,59.3,52.4,54.0,47.8,
Jaylen Warren,RB,PIT,9,58.4,6.1,62.4,73.1,117.0,77.2,
David Montgomery,RB,HOU,8,58.4,6.3,55.3,48.1,67.0,61.1,
Christian Watson,WR,GB,11,58.8,6.4,56.2,69.3,88.0,58.3,
Courtland Sutton,WR,DEN,10,59.6,6.4,58.9,80.8,72.0,83.3,Waddle's arrival caps the upside
TreVeyon Henderson,RB,NE,11,61.5,6.5,62.4,53.3,69.0,69.3,
DK Metcalf,WR,PIT,9,64.5,7.1,63.9,74.8,75.0,78.5,
Parker Washington,WR,JAX,7,64.8,7.5,65.2,76.3,76.0,65.7,
Tyler Warren,TE,IND,13,65.5,9.2,73.2,47.7,51.0,54.3,
Dak Prescott,QB,DAL,14,65.6,7.8,66.4,78.1,95.0,77.7,
Marvin Harrison Jr.,WR,ARI,14,65.8,7.6,65.2,75.9,74.0,68.4,
Rhamondre Stevenson,RB,NE,11,66.4,6.8,65.1,81.3,70.0,78.6,Clear lead back in camp per beat reports
Alec Pierce,WR,IND,13,66.7,8.3,60.2,101.9,77.0,100.6,On PUP after ankle surgery — falling
Tony Pollard,RB,TEN,9,69.3,6.7,67.7,83.5,119.0,84.3,
Jayden Daniels,QB,WAS,7,72.2,11.3,70.4,64.0,56.0,51.7,
Brian Thomas Jr.,WR,JAX,7,72.5,7.6,69.2,72.0,91.0,78.5,
Rico Dowdle,RB,PIT,9,74.5,7.5,74.2,85.0,118.0,88.7,
Michael Pittman Jr.,WR,PIT,9,74.8,7.8,80.9,102.7,73.0,80.4,
Kyle Pitts Sr.,TE,ATL,11,75.5,9.3,82.7,69.4,78.0,80.8,
Michael Wilson,WR,ARI,14,76.0,8.0,75.1,83.1,90.0,87.8,
Jadarian Price,RB,SEA,11,76.5,10.5,73.7,67.3,,73.1,Rookie
Matthew Stafford,QB,LAR,11,76.9,11.0,75.4,96.1,102.0,104.2,
Carnell Tate,WR,TEN,9,77.3,9.5,75.4,66.1,,67.4,Rookie
Jalen Hurts,QB,PHI,10,78.8,11.3,79.7,60.7,62.0,57.5,
Harold Fannin Jr.,TE,CLE,11,78.9,7.4,82.9,68.8,79.0,74.7,
Chuba Hubbard,RB,CAR,5,79.0,9.7,84.2,76.6,122.0,98.2,Hamstring; Brooks has taken over the committee
Chris Godwin Jr.,WR,TB,10,80.0,8.3,76.6,95.3,158.0,77.3,
Seattle,DST,SEA,11,83.2,8.2,80.6,,,,First DST off the board
Wan'Dale Robinson,WR,TEN,9,85.9,8.3,93.4,116.8,106.0,87.0,
Josh Downs,WR,IND,13,85.9,8.3,89.9,107.0,140.0,87.1,
Jakobi Meyers,WR,JAX,7,87.7,7.1,85.5,110.8,92.0,102.6,
Brock Purdy,QB,SF,8,88.0,12.1,89.3,121.4,99.0,95.9,
Denver,DST,DEN,10,88.1,7.5,92.1,,,,
RJ Harvey,RB,DEN,10,88.1,12.6,100.5,79.7,152.0,95.9,
J.K. Dobbins,RB,DEN,10,88.5,10.2,77.8,94.9,129.0,100.1,
Caleb Williams,QB,CHI,10,90.6,12.1,92.0,71.0,104.0,67.6,
Trevor Lawrence,QB,JAX,7,91.2,12.4,92.8,100.6,93.0,77.7,
Stefon Diggs,WR,WAS,7,91.2,8.8,95.8,113.6,154.0,96.1,Signed with Washington Aug 7 — ADP still catching up
Kenny Gainwell,RB,TB,10,91.5,10.2,99.8,111.6,120.0,100.7,
Jayden Reed,WR,GB,11,95.0,8.4,89.1,115.6,110.0,105.5,
Quentin Johnston,WR,LAC,7,96.3,9.1,87.7,111.2,139.0,96.8,
Jordan Addison,WR,MIN,6,97.4,8.7,86.8,104.3,107.0,106.7,
Jonathon Brooks,RB,CAR,5,97.5,14.2,92.2,109.3,121.0,91.3,Rising fast — has passed Hubbard on most boards
Houston,DST,HOU,8,98.4,9.3,101.5,,,,
Jared Goff,QB,DET,6,99.8,12.8,101.0,132.8,171.0,107.9,
Tucker Kraft,TE,GB,11,100.4,19.7,95.8,62.2,124.0,77.3,ACL recovery — biggest platform split on the board
Dallas Goedert,TE,PHI,10,101.0,20.7,101.6,123.3,126.0,118.1,
Aaron Jones Sr.,RB,MIN,6,101.8,12.4,108.0,124.6,143.0,113.8,
Khalil Shakir,WR,BUF,7,101.8,8.5,107.9,139.6,108.0,121.3,
Patrick Mahomes,QB,KC,5,102.7,12.4,101.5,106.7,114.0,100.4,
Xavier Worthy,WR,KC,5,105.6,8.0,98.9,139.1,111.0,127.9,
Justin Herbert,QB,LAC,7,105.9,14.1,108.7,82.3,112.0,72.3,
Matthew Golden,WR,GB,11,106.2,8.9,102.2,128.8,89.0,128.8,
Travis Kelce,TE,KC,5,106.7,20.8,117.4,92.4,127.0,97.7,
Sam LaPorta,TE,DET,6,106.7,23.3,117.2,59.6,80.0,85.3,
Kyle Monangai,RB,CHI,10,108.7,12.8,96.2,99.0,131.0,116.1,
LA Rams,DST,LAR,11,109.1,11.1,112.3,,,,
Minnesota,DST,MIN,6,109.4,9.6,109.5,,,,
Deebo Samuel Sr.,WR,SF,8,109.7,10.6,103.5,138.2,141.0,137.1,Back in SF after Pearsall was ruled out
Rachaad White,RB,WAS,7,111.2,13.8,121.1,131.3,133.0,109.7,
Bo Nix,QB,DEN,10,111.9,13.4,112.1,120.6,97.0,100.9,
Romeo Doubs,WR,NE,11,114.3,9.5,108.0,132.2,159.0,129.4,
Jacory Croskey-Merritt,RB,WAS,7,115.6,14.8,102.7,114.4,132.0,115.0,
Makai Lemon,WR,PHI,10,115.6,10.7,111.0,87.5,,109.9,Rookie
Jaxson Dart,QB,NYG,8,116.4,11.9,119.7,90.7,85.0,97.1,
George Kittle,TE,SF,8,116.7,19.7,118.2,90.3,125.0,98.7,
Jordan Mason,RB,MIN,6,117.4,16.0,114.8,117.7,150.0,117.5,
KC Concepcion,WR,CLE,11,120.9,10.8,116.2,122.3,,121.6,Rookie
Blake Corum,RB,LAR,11,121.4,17.2,121.5,103.1,151.0,112.1,
Jalen Coker,WR,CAR,5,121.6,9.9,117.0,144.4,163.0,124.7,
Jerry Jeudy,WR,CLE,11,126.1,12.3,124.2,190.3,184.0,166.5,
Mark Andrews,TE,BAL,13,127.8,18.8,123.4,129.2,135.0,130.2,
Detroit,DST,DET,6,128.6,14.9,129.6,,,,
Rashid Shaheed,WR,SEA,11,129.0,11.6,119.4,146.2,164.0,149.6,
Baker Mayfield,QB,TB,10,129.2,10.4,135.2,141.4,192.0,118.4,
Brandon Aubrey,K,DAL,14,129.4,21.2,132.9,,,,First kicker off the board
Tyjae Spears,RB,TEN,9,130.1,11.8,139.0,167.8,177.0,138.7,
New England,DST,NE,11,130.2,15.2,131.7,,,,
Jayden Higgins,WR,HOU,8,130.3,9.9,133.2,192.0,,,
Keenan Allen,WR,IND,13,131.8,12.4,134.4,197.7,213.0,194.2,Signed with Indy — caps Pierce and Downs
Tre Tucker,WR,LV,13,132.0,9.1,131.1,202.8,199.0,166.0,
Jake Ferguson,TE,DAL,14,132.7,17.4,144.5,97.1,134.0,116.9,
Pittsburgh,DST,PIT,9,133.0,14.8,134.1,,,,
Kyler Murray,QB,MIN,6,133.3,12.7,137.1,159.8,165.0,114.0,
Tyler Shough,QB,NO,8,134.0,13.1,138.3,181.4,167.0,125.2,
Zach Charbonnet,RB,SEA,11,134.2,15.5,132.6,145.0,175.0,154.3,
De'Zhaun Stribling,WR,SF,8,135.7,17.3,130.8,156.0,160.0,139.9,Rookie
Jalen McMillan,WR,TB,10,135.8,10.7,134.7,209.1,188.0,171.8,
Isaiah Likely,TE,NYG,8,136.6,17.8,134.4,108.9,145.0,122.1,
Jason Myers,K,SEA,11,138.6,19.4,140.8,,,,
Denzel Boston,WR,CLE,11,138.6,14.5,142.0,165.6,,151.6,Rookie
Jordyn Tyson,WR,NO,8,139.8,25.5,151.2,86.5,,132.1,Out ~2 months with a hamstring — late stash only
Philadelphia,DST,PHI,10,139.9,16.6,142.9,,,,
Ka'imi Fairbairn,K,HOU,8,140.0,18.1,126.5,,,,
LA Chargers,DST,LAC,7,140.9,14.8,140.7,,,,
Woody Marks,RB,HOU,8,140.9,15.3,139.8,162.6,153.0,136.8,
Sam Darnold,QB,SEA,11,142.4,14.2,148.5,176.4,280.0,141.2,
Cameron Dicker,K,LAC,7,142.6,18.3,144.7,,,,
Calvin Ridley,WR,TEN,9,142.9,11.8,136.1,253.2,189.0,202.7,
Juwan Johnson,TE,NO,8,143.5,21.3,163.4,187.1,210.0,136.4,
Alvin Kamara,RB,NO,8,145.3,23.0,152.3,160.7,176.0,158.6,
Jordan Love,QB,GB,11,146.8,13.5,151.5,148.2,273.0,119.4,
Jake Bates,K,DET,6,147.7,18.6,145.5,,,,
Jauan Jennings,WR,MIN,6,148.2,9.7,141.7,195.1,287.0,164.1,
Mike Washington Jr.,RB,LV,13,148.2,25.6,151.5,172.7,,163.1,Rookie — big riser this week
Harrison Mevis,K,LAR,11,148.4,17.2,150.4,,,,
Malik Washington,WR,MIA,6,148.7,13.4,147.1,200.7,305.0,191.5,
Dalton Kincaid,TE,BUF,7,148.9,21.4,151.5,89.2,146.0,112.6,
Cooper Kupp,WR,SEA,11,150.6,18.4,155.5,223.1,322.0,226.0,
Chase McLaughlin,K,TB,10,152.7,17.8,151.7,,,,
Tyler Allgeier,RB,ARI,14,153.3,21.9,154.5,146.0,182.0,139.5,
Oronde Gadsden,TE,LAC,7,153.3,24.3,,125.2,416.0,202.9,Wildest platform gap on the board
Jonah Coleman,RB,DEN,10,153.4,17.6,166.1,167.6,,157.4,Rookie
Hunter Henry,TE,NE,11,153.4,24.7,162.8,135.1,148.0,162.9,
Isiah Pacheco,RB,DET,6,153.9,19.3,154.9,185.2,202.0,162.6,Sprained MCL
Cam Little,K,JAX,7,154.0,17.7,151.1,,,,
MarShawn Lloyd,RB,GB,11,154.6,21.9,164.9,199.8,269.0,190.3,
Tyler Loop,K,BAL,13,154.6,15.0,147.8,,,,
Tyrone Tracy Jr.,RB,NYG,8,154.6,17.9,164.1,163.3,222.0,155.3,
Jalen Nailor,WR,LV,13,155.4,15.9,144.5,183.8,212.0,189.9,
Chig Okonkwo,TE,WAS,7,156.3,15.2,,186.1,311.0,159.9,
Tank Dell,WR,HOU,8,156.5,15.8,152.9,174.8,162.0,206.3,
Chris Rodriguez Jr.,RB,JAX,7,157.2,19.5,157.6,142.6,180.0,141.5,
Jacksonville,DST,JAX,7,157.4,14.7,161.4,,,,
Dylan Sampson,RB,CLE,11,157.7,15.3,172.9,188.2,220.0,146.6,
Keaton Mitchell,RB,LAC,7,157.7,20.4,158.3,180.0,200.0,153.1,
C.J. Stroud,QB,HOU,8,158.1,15.5,162.8,204.4,276.0,144.8,
Cleveland,DST,CLE,11,158.6,13.3,162.8,,,,
Braelon Allen,RB,NYJ,13,159.2,20.9,170.9,208.2,260.0,173.1,
Tank Bigsby,RB,PHI,10,159.6,21.0,155.2,177.5,205.0,174.0,
Kenyon Sadiq,TE,NYJ,13,160.0,16.5,175.7,150.8,,219.8,Rookie
Terrance Ferguson,TE,LAR,11,160.2,12.3,210.3,207.7,207.0,216.2,
Wil Lutz,K,DEN,10,160.5,16.4,160.1,,,,
Buffalo,DST,BUF,7,160.6,15.3,162.6,,,,
Daniel Jones,QB,IND,13,160.7,12.3,166.2,205.0,173.0,150.4,
Antonio Williams,WR,WAS,7,161.0,13.2,,237.9,,228.9,Rookie
Bryce Young,QB,CAR,5,161.9,19.5,,227.1,313.0,170.4,
Harrison Butker,K,KC,5,162.2,17.3,166.0,,,,
Brenton Strange,TE,JAX,7,162.7,20.2,170.1,152.8,263.0,159.1,
Devaughn Vele,WR,NO,8,162.7,17.6,167.9,239.8,283.0,249.2,
Jaylin Noel,WR,HOU,8,162.8,13.7,,213.7,219.0,194.3,
Malik Willis,QB,MIA,6,162.8,14.0,163.7,194.3,195.0,131.0,
Dalton Schultz,TE,HOU,8,163.0,19.7,174.1,193.9,297.0,166.2,
Adonai Mitchell,WR,NYJ,13,163.4,13.9,161.3,234.2,191.0,168.2,
Najee Harris,RB,NYG,8,163.6,17.7,,222.7,397.0,253.3,
Fernando Mendoza,QB,LV,13,164.0,28.3,,179.5,,258.7,Rookie
Brian Robinson,RB,ATL,11,164.1,24.9,166.2,153.2,181.0,181.1,
Baltimore,DST,BAL,13,165.3,19.8,167.7,,,,
Ja'Kobi Lane,WR,BAL,13,157.1,19.8,167.1,157.6,,234.4,Rookie
Kayshon Boutte,WR,HOU,8,166.5,19.3,165.6,235.0,304.0,188.8,
Dontayvion Wicks,WR,PHI,10,166.7,19.2,179.6,,215.0,190.3,
T.J. Hockenson,TE,MIN,6,166.8,21.4,169.5,164.6,138.0,188.4,
Jaydon Blue,RB,DAL,14,167.6,13.2,,229.5,340.0,224.7,
Cam Ward,QB,TEN,9,168.4,12.7,164.2,213.2,315.0,156.7,
Justice Hill,RB,BAL,13,168.4,12.2,171.4,,265.0,235.8,
James Conner,RB,ARI,14,169.1,13.4,,216.1,352.0,202.1,
Aaron Rodgers,QB,PIT,9,169.2,32.7,,228.8,385.0,222.7,
Travis Hunter,WR,JAX,7,169.4,11.8,163.5,166.8,109.0,186.4,
Evan McPherson,K,CIN,6,170.8,15.2,181.1,,,,
Troy Franklin,WR,DEN,10,177.2,19.6,185.5,241.8,977.0,210.9,
Darius Slayton,WR,NYG,8,178.9,11.0,164.9,,328.0,267.0,
Geno Smith,QB,NYJ,13,180.2,9.9,,,379.0,224.1,
Greg Dulcich,TE,MIA,6,183.0,22.9,,229.7,365.0,209.2,
Pat Freiermuth,TE,PIT,9,185.0,28.4,,239.9,296.0,238.1,
`.trim();

function parsePlayers() {
  const num = (v) => (v == null || v.trim() === "" ? null : parseFloat(v));
  return RAW.split("\n").map((line, i) => {
    const c = line.split(",");
    return {
      id: i, name: c[0].trim(), pos: c[1].trim(), team: c[2].trim(), bye: parseInt(c[3], 10),
      ffcPPR: num(c[4]), sd: num(c[5]), ffcHalf: num(c[6]),
      sleeper: num(c[7]), espn: num(c[8]), fpros: num(c[9]),
      note: (c[10] || "").trim() || null,
    };
  });
}
const PLAYERS = parsePlayers();

/* Built-in RotoBaller PPR projections (snapshot). Ships as the default so the
   board works out of the box; uploading a fresh CSV or syncing Sleeper overrides
   it. Refresh before draft day — projections move daily in the preseason. */
const BAKED_PROJ = {"jahmyr gibbs":{"pts":377,"targets":92,"carries":284,"touches":376,"tdShare":0.286},"bijan robinson":{"pts":375,"targets":109,"carries":286,"touches":395,"tdShare":0.192},"puka nacua":{"pts":337,"targets":175,"carries":12,"touches":187,"tdShare":0.178},"jamarr chase":{"pts":330,"targets":172,"carries":2,"touches":174,"tdShare":0.182},"jaxon smithnjigba":{"pts":333,"targets":156,"carries":5,"touches":161,"tdShare":0.162},"amonra st brown":{"pts":314,"targets":168,"carries":2,"touches":170,"tdShare":0.191},"christian mccaffrey":{"pts":321,"targets":104,"carries":279,"touches":383,"tdShare":0.224},"jonathan taylor":{"pts":312,"targets":53,"carries":347,"touches":400,"tdShare":0.25},"drake london":{"pts":283,"targets":156,"carries":0,"touches":156,"tdShare":0.191},"devon achane":{"pts":281,"targets":83,"carries":228,"touches":311,"tdShare":0.171},"ceedee lamb":{"pts":276,"targets":159,"carries":2,"touches":161,"tdShare":0.152},"justin jefferson":{"pts":272,"targets":174,"carries":1,"touches":175,"tdShare":0.154},"james cook":{"pts":269,"targets":39,"carries":303,"touches":342,"tdShare":0.245},"chase brown":{"pts":270,"targets":77,"carries":236,"touches":313,"tdShare":0.244},"rashee rice":{"pts":257,"targets":147,"carries":5,"touches":152,"tdShare":0.21},"ashton jeanty":{"pts":264,"targets":77,"carries":257,"touches":334,"tdShare":0.227},"derrick henry":{"pts":268,"targets":24,"carries":298,"touches":322,"tdShare":0.291},"aj brown":{"pts":254,"targets":143,"carries":0,"touches":143,"tdShare":0.165},"chris olave":{"pts":247,"targets":157,"carries":0,"touches":157,"tdShare":0.17},"saquon barkley":{"pts":266,"targets":58,"carries":306,"touches":364,"tdShare":0.226},"george pickens":{"pts":255,"targets":120,"carries":0,"touches":120,"tdShare":0.188},"nico collins":{"pts":251,"targets":136,"carries":1,"touches":137,"tdShare":0.167},"kenneth walker":{"pts":253,"targets":54,"carries":276,"touches":330,"tdShare":0.213},"omarion hampton":{"pts":253,"targets":63,"carries":229,"touches":292,"tdShare":0.261},"garrett wilson":{"pts":253,"targets":151,"carries":1,"touches":152,"tdShare":0.142},"zay flowers":{"pts":239,"targets":114,"carries":10,"touches":124,"tdShare":0.151},"malik nabers":{"pts":237,"targets":116,"carries":2,"touches":118,"tdShare":0.203},"jeremiyah love":{"pts":248,"targets":75,"carries":223,"touches":298,"tdShare":0.169},"trey mcbride":{"pts":240,"targets":150,"carries":0,"touches":150,"tdShare":0.15},"devonta smith":{"pts":229,"targets":135,"carries":0,"touches":135,"tdShare":0.131},"josh jacobs":{"pts":245,"targets":50,"carries":264,"touches":314,"tdShare":0.294},"kyren williams":{"pts":231,"targets":47,"carries":235,"touches":282,"tdShare":0.312},"tetairoa mcmillan":{"pts":236,"targets":133,"carries":0,"touches":133,"tdShare":0.153},"josh allen":{"pts":358,"targets":0,"carries":114,"touches":114,"tdShare":0.492},"emeka egbuka":{"pts":229,"targets":111,"carries":1,"touches":112,"tdShare":0.183},"breece hall":{"pts":231,"targets":62,"carries":253,"touches":315,"tdShare":0.182},"brock bowers":{"pts":250,"targets":140,"carries":0,"touches":140,"tdShare":0.168},"javonte williams":{"pts":231,"targets":46,"carries":254,"touches":300,"tdShare":0.286},"tee higgins":{"pts":223,"targets":117,"carries":0,"touches":117,"tdShare":0.215},"cam skattebo":{"pts":239,"targets":56,"carries":231,"touches":287,"tdShare":0.251},"ladd mcconkey":{"pts":223,"targets":122,"carries":0,"touches":122,"tdShare":0.188},"travis etienne":{"pts":233,"targets":61,"carries":243,"touches":304,"tdShare":0.206},"davante adams":{"pts":220,"targets":127,"carries":0,"touches":127,"tdShare":0.273},"jameson williams":{"pts":219,"targets":101,"carries":7,"touches":108,"tdShare":0.164},"bucky irving":{"pts":217,"targets":53,"carries":247,"touches":300,"tdShare":0.221},"dandre swift":{"pts":236,"targets":51,"carries":224,"touches":275,"tdShare":0.28},"jaylen waddle":{"pts":204,"targets":120,"carries":1,"touches":121,"tdShare":0.147},"terry mclaurin":{"pts":212,"targets":107,"carries":0,"touches":107,"tdShare":0.198},"dj moore":{"pts":206,"targets":117,"carries":9,"touches":126,"tdShare":0.204},"quinshon judkins":{"pts":213,"targets":43,"carries":292,"touches":335,"tdShare":0.254},"drake maye":{"pts":305,"targets":0,"carries":100,"touches":100,"tdShare":0.4},"bhayshul tuten":{"pts":200,"targets":38,"carries":227,"touches":265,"tdShare":0.3},"rome odunze":{"pts":203,"targets":93,"carries":1,"touches":94,"tdShare":0.207},"mike evans":{"pts":199,"targets":140,"carries":0,"touches":140,"tdShare":0.181},"lamar jackson":{"pts":326,"targets":0,"carries":116,"touches":116,"tdShare":0.423},"colston loveland":{"pts":215,"targets":117,"carries":0,"touches":117,"tdShare":0.167},"joe burrow":{"pts":309,"targets":0,"carries":46,"touches":46,"tdShare":0.434},"luther burden":{"pts":200,"targets":95,"carries":7,"touches":102,"tdShare":0.15},"jaylen warren":{"pts":202,"targets":55,"carries":175,"touches":230,"tdShare":0.178},"david montgomery":{"pts":214,"targets":42,"carries":218,"touches":260,"tdShare":0.28},"christian watson":{"pts":191,"targets":85,"carries":1,"touches":86,"tdShare":0.22},"courtland sutton":{"pts":206,"targets":109,"carries":0,"touches":109,"tdShare":0.204},"treveyon henderson":{"pts":184,"targets":41,"carries":185,"touches":226,"tdShare":0.228},"dk metcalf":{"pts":192,"targets":106,"carries":1,"touches":107,"tdShare":0.188},"parker washington":{"pts":185,"targets":105,"carries":1,"touches":106,"tdShare":0.195},"tyler warren":{"pts":192,"targets":111,"carries":0,"touches":111,"tdShare":0.156},"dak prescott":{"pts":275,"targets":0,"carries":51,"touches":51,"tdShare":0.429},"marvin harrison":{"pts":190,"targets":123,"carries":0,"touches":123,"tdShare":0.189},"rhamondre stevenson":{"pts":182,"targets":53,"carries":171,"touches":224,"tdShare":0.231},"alec pierce":{"pts":187,"targets":105,"carries":0,"touches":105,"tdShare":0.193},"tony pollard":{"pts":185,"targets":41,"carries":238,"touches":279,"tdShare":0.195},"jayden daniels":{"pts":325,"targets":0,"carries":133,"touches":133,"tdShare":0.382},"brian thomas":{"pts":173,"targets":102,"carries":2,"touches":104,"tdShare":0.173},"rico dowdle":{"pts":181,"targets":41,"carries":193,"touches":234,"tdShare":0.232},"michael pittman":{"pts":198,"targets":116,"carries":0,"touches":116,"tdShare":0.152},"kyle pitts":{"pts":199,"targets":114,"carries":0,"touches":114,"tdShare":0.121},"michael wilson":{"pts":175,"targets":115,"carries":0,"touches":115,"tdShare":0.137},"jadarian price":{"pts":188,"targets":35,"carries":222,"touches":257,"tdShare":0.255},"matthew stafford":{"pts":299,"targets":0,"carries":23,"touches":23,"tdShare":0.468},"carnell tate":{"pts":175,"targets":99,"carries":1,"touches":100,"tdShare":0.137},"jalen hurts":{"pts":316,"targets":0,"carries":106,"touches":106,"tdShare":0.468},"harold fannin":{"pts":197,"targets":121,"carries":0,"touches":121,"tdShare":0.152},"chuba hubbard":{"pts":163,"targets":46,"carries":184,"touches":230,"tdShare":0.221},"chris godwin":{"pts":176,"targets":97,"carries":0,"touches":97,"tdShare":0.17},"dst|sea":{"pts":164,"targets":0,"carries":0,"touches":0,"tdShare":null},"wandale robinson":{"pts":182,"targets":129,"carries":2,"touches":131,"tdShare":0.099},"josh downs":{"pts":159,"targets":100,"carries":1,"touches":101,"tdShare":0.113},"jakobi meyers":{"pts":174,"targets":103,"carries":4,"touches":107,"tdShare":0.138},"brock purdy":{"pts":302,"targets":0,"carries":71,"touches":71,"tdShare":0.444},"dst|den":{"pts":156,"targets":0,"carries":0,"touches":0,"tdShare":null},"rj harvey":{"pts":161,"targets":62,"carries":103,"touches":165,"tdShare":0.261},"jk dobbins":{"pts":177,"targets":25,"carries":230,"touches":255,"tdShare":0.271},"caleb williams":{"pts":302,"targets":0,"carries":73,"touches":73,"tdShare":0.384},"trevor lawrence":{"pts":289,"targets":0,"carries":73,"touches":73,"tdShare":0.429},"stefon diggs":{"pts":159,"targets":89,"carries":0,"touches":89,"tdShare":0.151},"kenny gainwell":{"pts":155,"targets":58,"carries":107,"touches":165,"tdShare":0.155},"jayden reed":{"pts":181,"targets":98,"carries":12,"touches":110,"tdShare":0.166},"quentin johnston":{"pts":168,"targets":89,"carries":1,"touches":90,"tdShare":0.214},"jordan addison":{"pts":165,"targets":110,"carries":3,"touches":113,"tdShare":0.182},"jonathon brooks":{"pts":161,"targets":38,"carries":177,"touches":215,"tdShare":0.224},"dst|hou":{"pts":160,"targets":0,"carries":0,"touches":0,"tdShare":null},"jared goff":{"pts":274,"targets":0,"carries":22,"touches":22,"tdShare":0.423},"tucker kraft":{"pts":178,"targets":85,"carries":0,"touches":85,"tdShare":0.202},"dallas goedert":{"pts":184,"targets":93,"carries":0,"touches":93,"tdShare":0.261},"aaron jones":{"pts":161,"targets":63,"carries":160,"touches":223,"tdShare":0.149},"khalil shakir":{"pts":162,"targets":94,"carries":1,"touches":95,"tdShare":0.148},"patrick mahomes":{"pts":291,"targets":0,"carries":59,"touches":59,"tdShare":0.419},"xavier worthy":{"pts":167,"targets":96,"carries":15,"touches":111,"tdShare":0.18},"justin herbert":{"pts":305,"targets":0,"carries":82,"touches":82,"tdShare":0.393},"matthew golden":{"pts":158,"targets":88,"carries":5,"touches":93,"tdShare":0.152},"travis kelce":{"pts":175,"targets":99,"carries":0,"touches":99,"tdShare":0.137},"sam laporta":{"pts":174,"targets":95,"carries":0,"touches":95,"tdShare":0.172},"kyle monangai":{"pts":155,"targets":36,"carries":188,"touches":224,"tdShare":0.232},"dst|lar":{"pts":109,"targets":0,"carries":0,"touches":0,"tdShare":null},"dst|min":{"pts":112,"targets":0,"carries":0,"touches":0,"tdShare":null},"deebo samuel":{"pts":163,"targets":80,"carries":11,"touches":91,"tdShare":0.184},"rachaad white":{"pts":158,"targets":47,"carries":141,"touches":188,"tdShare":0.228},"bo nix":{"pts":291,"targets":0,"carries":79,"touches":79,"tdShare":0.419},"romeo doubs":{"pts":162,"targets":92,"carries":0,"touches":92,"tdShare":0.185},"jacory croskeymerritt":{"pts":138,"targets":14,"carries":195,"touches":209,"tdShare":0.304},"makai lemon":{"pts":144,"targets":81,"carries":1,"touches":82,"tdShare":0.167},"jaxson dart":{"pts":301,"targets":0,"carries":98,"touches":98,"tdShare":0.399},"george kittle":{"pts":187,"targets":99,"carries":0,"touches":99,"tdShare":0.193},"jordan mason":{"pts":147,"targets":18,"carries":200,"touches":218,"tdShare":0.245},"kc concepcion":{"pts":161,"targets":88,"carries":4,"touches":92,"tdShare":0.112},"blake corum":{"pts":129,"targets":18,"carries":155,"touches":173,"tdShare":0.233},"jalen coker":{"pts":149,"targets":84,"carries":0,"touches":84,"tdShare":0.161},"jerry jeudy":{"pts":146,"targets":80,"carries":1,"touches":81,"tdShare":0.123},"mark andrews":{"pts":163,"targets":87,"carries":0,"touches":87,"tdShare":0.258},"dst|det":{"pts":106,"targets":0,"carries":0,"touches":0,"tdShare":null},"rashid shaheed":{"pts":157,"targets":78,"carries":13,"touches":91,"tdShare":0.153},"baker mayfield":{"pts":271,"targets":0,"carries":56,"touches":56,"tdShare":0.406},"brandon aubrey":{"pts":179,"targets":0,"carries":0,"touches":0,"tdShare":null},"tyjae spears":{"pts":136,"targets":57,"carries":93,"touches":150,"tdShare":0.176},"dst|ne":{"pts":104,"targets":0,"carries":0,"touches":0,"tdShare":null},"keenan allen":{"pts":146,"targets":89,"carries":0,"touches":89,"tdShare":0.123},"tre tucker":{"pts":133,"targets":83,"carries":11,"touches":94,"tdShare":0.135},"jake ferguson":{"pts":167,"targets":105,"carries":0,"touches":105,"tdShare":0.18},"dst|pit":{"pts":127,"targets":0,"carries":0,"touches":0,"tdShare":null},"kyler murray":{"pts":259,"targets":0,"carries":82,"touches":82,"tdShare":0.44},"tyler shough":{"pts":264,"targets":0,"carries":74,"touches":74,"tdShare":0.394},"zach charbonnet":{"pts":126,"targets":26,"carries":130,"touches":156,"tdShare":0.286},"dezhaun stribling":{"pts":151,"targets":77,"carries":1,"touches":78,"tdShare":0.159},"jalen mcmillan":{"pts":124,"targets":64,"carries":4,"touches":68,"tdShare":0.145},"isaiah likely":{"pts":169,"targets":88,"carries":0,"touches":88,"tdShare":0.178},"jason myers":{"pts":193,"targets":0,"carries":0,"touches":0,"tdShare":null},"denzel boston":{"pts":146,"targets":93,"carries":1,"touches":94,"tdShare":0.123},"jordyn tyson":{"pts":132,"targets":68,"carries":1,"touches":69,"tdShare":0.136},"dst|phi":{"pts":116,"targets":0,"carries":0,"touches":0,"tdShare":null},"kaimi fairbairn":{"pts":172,"targets":0,"carries":0,"touches":0,"tdShare":null},"dst|lac":{"pts":92,"targets":0,"carries":0,"touches":0,"tdShare":null},"woody marks":{"pts":117,"targets":36,"carries":143,"touches":179,"tdShare":0.154},"sam darnold":{"pts":264,"targets":0,"carries":43,"touches":43,"tdShare":0.485},"cameron dicker":{"pts":153,"targets":0,"carries":0,"touches":0,"tdShare":null},"calvin ridley":{"pts":138,"targets":67,"carries":4,"touches":71,"tdShare":0.13},"juwan johnson":{"pts":153,"targets":86,"carries":0,"touches":86,"tdShare":0.118},"alvin kamara":{"pts":134,"targets":47,"carries":119,"touches":166,"tdShare":0.179},"jordan love":{"pts":273,"targets":0,"carries":43,"touches":43,"tdShare":0.44},"jake bates":{"pts":137,"targets":0,"carries":0,"touches":0,"tdShare":null},"jauan jennings":{"pts":138,"targets":74,"carries":0,"touches":74,"tdShare":0.217},"mike washington":{"pts":96,"targets":24,"carries":128,"touches":152,"tdShare":0.188},"harrison mevis":{"pts":163,"targets":0,"carries":0,"touches":0,"tdShare":null},"malik washington":{"pts":110,"targets":65,"carries":13,"touches":78,"tdShare":0.109},"dalton kincaid":{"pts":163,"targets":80,"carries":0,"touches":80,"tdShare":0.184},"cooper kupp":{"pts":124,"targets":71,"carries":1,"touches":72,"tdShare":0.145},"chase mclaughlin":{"pts":154,"targets":0,"carries":0,"touches":0,"tdShare":null},"tyler allgeier":{"pts":105,"targets":23,"carries":110,"touches":133,"tdShare":0.286},"oronde gadsden":{"pts":92,"targets":53,"carries":0,"touches":53,"tdShare":0.13},"jonah coleman":{"pts":43,"targets":13,"carries":51,"touches":64,"tdShare":0.14},"hunter henry":{"pts":151,"targets":84,"carries":0,"touches":84,"tdShare":0.199},"isiah pacheco":{"pts":120,"targets":29,"carries":130,"touches":159,"tdShare":0.2},"cam little":{"pts":149,"targets":0,"carries":0,"touches":0,"tdShare":null},"marshawn lloyd":{"pts":44,"targets":11,"carries":61,"touches":72,"tdShare":0.136},"tyler loop":{"pts":136,"targets":0,"carries":0,"touches":0,"tdShare":null},"tyrone tracy":{"pts":102,"targets":33,"carries":99,"touches":132,"tdShare":0.176},"jalen nailor":{"pts":130,"targets":78,"carries":2,"touches":80,"tdShare":0.185},"chig okonkwo":{"pts":126,"targets":72,"carries":0,"touches":72,"tdShare":0.095},"tank dell":{"pts":143,"targets":72,"carries":9,"touches":81,"tdShare":0.126},"chris rodriguez":{"pts":96,"targets":6,"carries":128,"touches":134,"tdShare":0.313},"dst|jax":{"pts":98,"targets":0,"carries":0,"touches":0,"tdShare":null},"dylan sampson":{"pts":100,"targets":48,"carries":69,"touches":117,"tdShare":0.12},"keaton mitchell":{"pts":87,"targets":21,"carries":89,"touches":110,"tdShare":0.207},"cj stroud":{"pts":253,"targets":0,"carries":53,"touches":53,"tdShare":0.387},"dst|cle":{"pts":118,"targets":0,"carries":0,"touches":0,"tdShare":null},"braelon allen":{"pts":96,"targets":18,"carries":114,"touches":132,"tdShare":0.313},"tank bigsby":{"pts":83,"targets":5,"carries":103,"touches":108,"tdShare":0.289},"kenyon sadiq":{"pts":120,"targets":77,"carries":0,"touches":77,"tdShare":0.15},"terrance ferguson":{"pts":125,"targets":58,"carries":0,"touches":58,"tdShare":0.24},"wil lutz":{"pts":137,"targets":0,"carries":0,"touches":0,"tdShare":null},"dst|buf":{"pts":109,"targets":0,"carries":0,"touches":0,"tdShare":null},"daniel jones":{"pts":263,"targets":0,"carries":61,"touches":61,"tdShare":0.403},"bryce young":{"pts":235,"targets":0,"carries":56,"touches":56,"tdShare":0.409},"harrison butker":{"pts":140,"targets":0,"carries":0,"touches":0,"tdShare":null},"brenton strange":{"pts":146,"targets":84,"carries":0,"touches":84,"tdShare":0.164},"devaughn vele":{"pts":121,"targets":73,"carries":0,"touches":73,"tdShare":0.149},"jaylin noel":{"pts":109,"targets":62,"carries":4,"touches":66,"tdShare":0.165},"malik willis":{"pts":244,"targets":0,"carries":122,"touches":122,"tdShare":0.352},"dalton schultz":{"pts":147,"targets":87,"carries":0,"touches":87,"tdShare":0.122},"adonai mitchell":{"pts":112,"targets":65,"carries":1,"touches":66,"tdShare":0.161},"najee harris":{"pts":58,"targets":11,"carries":72,"touches":83,"tdShare":0.207},"fernando mendoza":{"pts":181,"targets":0,"carries":49,"touches":49,"tdShare":0.365},"brian robinson":{"pts":94,"targets":9,"carries":121,"touches":130,"tdShare":0.255},"dst|bal":{"pts":94,"targets":0,"carries":0,"touches":0,"tdShare":null},"jakobi lane":{"pts":130,"targets":62,"carries":1,"touches":63,"tdShare":0.231},"kayshon boutte":{"pts":110,"targets":49,"carries":0,"touches":49,"tdShare":0.218},"dontayvion wicks":{"pts":105,"targets":57,"carries":0,"touches":57,"tdShare":0.114},"tj hockenson":{"pts":140,"targets":97,"carries":0,"touches":97,"tdShare":0.129},"jaydon blue":{"pts":47,"targets":11,"carries":60,"touches":71,"tdShare":0.128},"cam ward":{"pts":235,"targets":0,"carries":43,"touches":43,"tdShare":0.383},"justice hill":{"pts":105,"targets":40,"carries":58,"touches":98,"tdShare":0.171},"james conner":{"pts":57,"targets":23,"carries":43,"touches":66,"tdShare":0.211},"aaron rodgers":{"pts":224,"targets":0,"carries":26,"touches":26,"tdShare":0.393},"travis hunter":{"pts":112,"targets":55,"carries":1,"touches":56,"tdShare":0.161},"evan mcpherson":{"pts":144,"targets":0,"carries":0,"touches":0,"tdShare":null},"troy franklin":{"pts":100,"targets":55,"carries":2,"touches":57,"tdShare":0.24},"darius slayton":{"pts":96,"targets":59,"carries":1,"touches":60,"tdShare":0.125},"geno smith":{"pts":220,"targets":0,"carries":56,"touches":56,"tdShare":0.427},"greg dulcich":{"pts":134,"targets":72,"carries":0,"touches":72,"tdShare":0.134},"pat freiermuth":{"pts":130,"targets":74,"carries":0,"touches":74,"tdShare":0.185}};

/* ---- math ---- */
function normCdf(z) {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989422804014327 * Math.exp(-0.5 * z * z);
  let p = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  p = 1 - p;
  return z >= 0 ? p : 1 - p;
}

const SOURCES = [
  { key: "market", label: "Market blend" }, { key: "ffc", label: "FFC mocks" },
  { key: "sleeper", label: "Sleeper" }, { key: "espn", label: "ESPN" }, { key: "fpros", label: "FantasyPros" },
];
function adpOf(p, source, scoring) {
  const ffc = scoring === "half" ? p.ffcHalf ?? p.ffcPPR : p.ffcPPR;
  if (source === "ffc") return ffc;
  if (source === "sleeper") return p.sleeper ?? ffc;
  if (source === "espn") return p.espn ?? ffc;
  if (source === "fpros") return p.fpros ?? ffc;
  const vals = [ffc, p.sleeper, p.espn, p.fpros].filter((v) => v != null && v < 400);
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : ffc;
}
const sdOf = (p, adp) => p.sd ?? Math.max(6, 0.16 * adp);

/* ---- name matching (shared by projections + sync) ---- */
const norm = (s) =>
  s.toLowerCase().replace(/[.'`-]/g, "").replace(/\s+(jr|sr|ii|iii|iv|v)$/g, "").trim();

/* ---- CSV parsing for projection imports (handles quoted fields) ---- */
function parseCsv(text) {
  const rows = []; let i = 0, field = "", row = [], inQ = false;
  while (i < text.length) {
    const ch = text[i];
    if (inQ) {
      if (ch === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else inQ = false; }
      else field += ch;
    } else if (ch === '"') inQ = true;
    else if (ch === ",") { row.push(field); field = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (field !== "" || row.length) { row.push(field); rows.push(row); row = []; field = ""; }
      if (ch === "\r" && text[i + 1] === "\n") i++;
    } else field += ch;
    i++;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.length && r.some((c) => c.trim() !== ""));
}

/* Turn a parsed projection CSV into the same {key -> {pts,targets,...}} shape
   the Sleeper pull produces. Auto-detects columns so it works across sources. */
function projFromCsv(text, scoring) {
  const rows = parseCsv(text);
  if (rows.length < 2) throw new Error("empty");
  const H = rows[0].map((h) => h.toLowerCase().replace(/[^a-z0-9]/g, ""));
  const find = (cands) => {
    for (const c of cands) { const j = H.findIndex((h) => h === c); if (j >= 0) return j; }
    for (const c of cands) { const j = H.findIndex((h) => h.includes(c)); if (j >= 0) return j; }
    return -1;
  };
  const iName = find(["player", "playername", "name"]);
  const iPos = find(["position", "pos"]);
  const iTeam = find(["team", "tm"]);
  const iPts = find(
    scoring === "half"
      ? ["ptshalfppr", "halfppr", "fptshalf", "projhalf", "fpts", "fantasypoints", "points", "projpts", "projection", "proj", "pts"]
      : ["ptsppr", "pprpts", "fptsppr", "projppr", "fpts", "fantasypoints", "points", "projpts", "projection", "proj", "pts"]
  );
  if (iName < 0 || iPts < 0) throw new Error("Couldn't find name and points columns in that CSV.");
  const iTgt = find(["targets", "target", "tgt", "rectgt"]);
  const iCar = find(["carries", "rushatt", "rushingatt", "att", "car"]);
  const iRecTD = find(["rectd", "retd", "receivingtd"]);
  const iRushTD = find(["rushtd", "rutd", "rushingtd"]);
  const iPassTD = find(["passtd", "ptd", "passingtd"]);
  const iTgtShare = find(["targetshare", "tgtshare", "tgtsh"]);
  const iAir = find(["airyards", "airyd", "airyardshare"]);

  const num = (r, j) => (j >= 0 && r[j] != null && r[j].trim() !== "" ? parseFloat(r[j].replace(/[^0-9.\-]/g, "")) || 0 : 0);
  const out = {}; let count = 0;
  const found = { points: true, targets: iTgt >= 0, carries: iCar >= 0, targetShare: iTgtShare >= 0, airYards: iAir >= 0 };
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const nm = (row[iName] || "").trim();
    if (!nm) continue;
    const pts = num(row, iPts);
    if (!pts) continue;
    const pos = (iPos >= 0 ? row[iPos] : "").toUpperCase();
    const team = (iTeam >= 0 ? row[iTeam] : "").toLowerCase().trim();
    const tgt = num(row, iTgt), car = num(row, iCar);
    const tdPts = (num(row, iRecTD) + num(row, iRushTD)) * 6 + num(row, iPassTD) * 4;
    const rec = {
      pts, targets: iTgt >= 0 ? tgt : null, carries: iCar >= 0 ? car : null,
      touches: iTgt >= 0 || iCar >= 0 ? tgt + car : null,
      tdShare: tdPts > 0 ? tdPts / pts : null,
      targetShare: iTgtShare >= 0 ? num(row, iTgtShare) : null,
      airYards: iAir >= 0 ? num(row, iAir) : null,
    };
    if (/^(dst|def|dstdef|dst\/def|d\/st)$/i.test(pos.replace(/\s/g, "")) && team) out["dst|" + team] = rec;
    else out[norm(nm)] = rec;
    count++;
  }
  // spelling variants: CSV name -> our dataset name, so nothing falls through
  const ALIAS = { "kenneth gainwell": "kenny gainwell", "chigoziem okonkwo": "chig okonkwo" };
  Object.entries(ALIAS).forEach(([csv, ours]) => { if (out[csv]) out[ours] = out[csv]; });
  return { map: out, count, found };
}

/* ---- 2. Sleeper projections ----------------------------------------------
   Undocumented but public; runs from the browser. Tries both hosts, reads the
   stat line defensively, returns a map keyed the same way as our name index. */
async function fetchSleeperProjections(scoring) {
  const ptsKey = scoring === "half" ? "pts_half_ppr" : "pts_ppr";
  const qs =
    "season_type=regular&order_by=pts_ppr" +
    ["QB", "RB", "WR", "TE", "K", "DEF"].map((p) => `&position[]=${p}`).join("");
  const hosts = [
    `https://api.sleeper.com/projections/nfl/2026?${qs}`,
    `https://api.sleeper.app/projections/nfl/2026?${qs}`,
  ];
  let rows = null;
  for (const url of hosts) {
    try {
      const r = await fetch(url);
      if (!r.ok) continue;
      const j = await r.json();
      if (Array.isArray(j) && j.length) { rows = j; break; }
    } catch (e) { /* try next host */ }
  }
  if (!rows) throw new Error("no-data");

  // Sleeper's rows sometimes carry a nested player, sometimes only a player_id.
  // If any lack a name, pull the player directory once and use it to resolve them
  // so matching is reliable either way.
  let dir = null;
  if (rows.some((r) => !(r.player && (r.player.last_name || r.player.first_name)))) {
    try { dir = await (await fetch("https://api.sleeper.app/v1/players/nfl")).json(); } catch (e) { /* names from rows only */ }
  }

  const out = {};
  for (const row of rows) {
    const st = row.stats || row;
    const pts = st[ptsKey] ?? st.pts_ppr ?? st.pts_std;
    if (pts == null) continue;
    let pl = row.player || {};
    if (!(pl.last_name || pl.first_name) && dir && row.player_id != null) pl = dir[row.player_id] || pl;
    const pos = (pl.position || row.position || "").toUpperCase();
    const rec_tgt = st.rec_tgt ?? st.tgt ?? 0;
    const rush_att = st.rush_att ?? 0;
    const tdPts = (st.rec_td ?? 0) * 6 + (st.rush_td ?? 0) * 6 + (st.pass_td ?? 0) * 4;
    const rec = { pts, targets: rec_tgt, carries: rush_att, touches: rec_tgt + rush_att, tdShare: pts > 0 ? tdPts / pts : 0 };
    const team = (pl.team || row.team || "").toLowerCase();
    if (pos === "DEF" || pos === "DST") { if (team) out["dst|" + team] = rec; continue; }
    const nm = norm(`${pl.first_name || ""} ${pl.last_name || ""}`);
    if (nm) out[nm] = rec;
  }
  return out;
}

/* ---- roster / snake helpers ---- */
const DEFAULT_ROSTER = { QB: 1, RB: 2, WR: 3, TE: 1, FLEX: 1, K: 1, DST: 1, BE: 6 };
function snakePicks(slot, teams, rounds) {
  const out = [];
  for (let r = 1; r <= rounds; r++)
    out.push(r % 2 === 1 ? (r - 1) * teams + slot : (r - 1) * teams + (teams - slot + 1));
  return out;
}

/* ---- UI atoms ---- */
const PosTag = ({ pos, rank }) => (
  <span className="inline-block rounded px-1.5 py-0.5 text-xs font-bold"
        style={{ background: POS_COLOR[pos] + "22", color: POS_COLOR[pos], fontFamily: MONO }}>
    {pos}{rank || ""}
  </span>
);
const Bar = ({ value, color }) => (
  <div className="h-1.5 w-full overflow-hidden rounded-sm" style={{ background: THEME.line }}>
    <div className="h-full rounded-sm" style={{ width: `${Math.max(0, Math.min(100, value * 100))}%`, background: color }} />
  </div>
);
const Chip = ({ on, label }) => (
  <span className="rounded px-1.5 py-0.5" style={{ background: on ? THEME.signal + "1f" : THEME.panel2, color: on ? THEME.signal : THEME.dim }}>
    {label}
  </span>
);
const Stat = ({ label, value, big, color }) => (
  <div>
    <div className="text-xs uppercase" style={{ color: THEME.muted, letterSpacing: "0.1em" }}>{label}</div>
    <div className={big ? "text-2xl font-black" : "text-lg font-bold"} style={{ fontFamily: MONO, color: color || THEME.chalk }}>{value}</div>
  </div>
);
function Select({ label, value, onChange, options, labels }) {
  return (
    <label className="flex items-center gap-1" style={{ color: THEME.muted }}>
      <span className="uppercase" style={{ letterSpacing: "0.08em" }}>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="rounded px-1.5 py-1 outline-none"
              style={{ background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk, fontFamily: MONO }}>
        {options.map((o, i) => (
          <option key={o} value={o} style={{ background: THEME.panel2 }}>{labels ? labels[i] : String(o).toUpperCase()}</option>
        ))}
      </select>
    </label>
  );
}

/* ==========================================================================
   MAIN
   ========================================================================== */
export default function DraftWarRoom() {
  /* settings */
  const [teams, setTeams] = useState(12);
  const [rounds, setRounds] = useState(16);
  const [slot, setSlot] = useState(6);
  const [scoring, setScoring] = useState("ppr");
  const [source, setSource] = useState("market");
  const [roster, setRoster] = useState(DEFAULT_ROSTER);

  /* draft state */
  const [taken, setTaken] = useState({});      // id -> "me" | "them"
  const [order, setOrder] = useState([]);       // ids in pick order
  const [owners, setOwners] = useState({});     // id -> team slot (overrides snake)
  const [prices, setPrices] = useState({});     // id -> auction price
  const [teamNames, setTeamNames] = useState({});

  /* projections */
  const [proj, setProj] = useState(BAKED_PROJ);         // name-key -> {pts,targets,carries,touches,tdShare}
  const [projState, setProjState] = useState({ status: "idle", msg: "" });

  /* auction ui */
  const [budget, setBudget] = useState(200);
  const [bidPlayer, setBidPlayer] = useState(null);
  const [bidAmt, setBidAmt] = useState("");

  /* sleeper */
  const [sleeperUser, setSleeperUser] = useState("");
  const [sleeperId, setSleeperId] = useState("");
  const [leagues, setLeagues] = useState([]);
  const [draftId, setDraftId] = useState("");
  const [autoSync, setAutoSync] = useState(false);
  const syncedCount = useRef(-1);
  const [syncState, setSyncState] = useState({ status: "idle", msg: "" });

  /* view */
  const [tab, setTab] = useState("board");
  const [posFilter, setPosFilter] = useState("ALL");
  const [query, setQuery] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saveNote, setSaveNote] = useState("");

  /* ---- persistence ---- */
  useEffect(() => {
    (async () => {
      try {
        const r = await window.storage.get("ff26:v2");
        if (r?.value) {
          const s = JSON.parse(r.value);
          setTeams(s.teams ?? 12); setRounds(s.rounds ?? 16); setSlot(s.slot ?? 6);
          setScoring(s.scoring ?? "ppr"); setSource(s.source ?? "market"); setRoster(s.roster ?? DEFAULT_ROSTER);
          setTaken(s.taken ?? {}); setOrder(s.order ?? []); setOwners(s.owners ?? {}); setPrices(s.prices ?? {});
          setTeamNames(s.teamNames ?? {}); setBudget(s.budget ?? 200);
          setDraftId(s.draftId ?? ""); setSleeperUser(s.sleeperUser ?? "");
          if (s.proj && Object.keys(s.proj).length) setProj(s.proj);
        }
      } catch (e) { /* fresh */ }
      setLoaded(true);
    })();
  }, []);
  useEffect(() => {
    if (!loaded) return;
    const t = setTimeout(async () => {
      try {
        await window.storage.set("ff26:v2", JSON.stringify({
          teams, rounds, slot, scoring, source, roster, taken, order, owners, prices,
          teamNames, budget, draftId, sleeperUser, proj,
        }));
        setSaveNote("Saved"); setTimeout(() => setSaveNote(""), 1000);
      } catch (e) { setSaveNote("Storage unavailable"); }
    }, 400);
    return () => clearTimeout(t);
  }, [loaded, teams, rounds, slot, scoring, source, roster, taken, order, owners, prices, teamNames, budget, draftId, sleeperUser, proj]);

  useEffect(() => { if (slot > teams) setSlot(teams); }, [teams, slot]);

  /* ---- pick geometry ---- */
  const myPicks = useMemo(() => snakePicks(slot, teams, rounds), [slot, teams, rounds]);
  const currentPick = order.length + 1;
  const nextMine = myPicks.find((p) => p >= currentPick) ?? null;
  const afterNext = myPicks.find((p) => p > (nextMine ?? 0)) ?? null;
  const onClock = nextMine === currentPick;
  const rd = (n) => `${Math.floor((n - 1) / teams) + 1}.${String(((n - 1) % teams) + 1).padStart(2, "0")}`;
  const teamAtPick = useCallback((k) => {
    const r = Math.floor((k - 1) / teams) + 1, i = ((k - 1) % teams) + 1;
    return r % 2 === 1 ? i : teams - i + 1;
  }, [teams]);

  const hasProj = useMemo(() => Object.keys(proj).length > 0, [proj]);

  /* ---- 3. VALUE ENGINE: projections -> VORP -> value rank -> edge ---- */
  const enriched = useMemo(() => {
    const rows = PLAYERS.map((p) => {
      const adp = adpOf(p, source, scoring);
      const key = p.pos === "DST" ? "dst|" + p.team.toLowerCase() : norm(p.name);
      const pr = proj[key];
      // projected points: real if synced, else ADP-implied so the tool still works
      const projPts = pr ? pr.pts : 100 * Math.exp(-adp / 50);
      return { ...p, adp, sdv: sdOf(p, adp), projPts, real: !!pr,
               targets: pr?.targets ?? null, carries: pr?.carries ?? null,
               touches: pr?.touches ?? null, tdShare: pr?.tdShare ?? null };
    }).sort((a, b) => a.adp - b.adp);

    rows.forEach((p, i) => (p.adpRank = i + 1));

    // positional rank + tiers (by ADP, for run/scarcity reads)
    const byPos = {};
    rows.forEach((p) => (byPos[p.pos] = byPos[p.pos] || []).push(p));
    Object.values(byPos).forEach((list) => {
      let tier = 1;
      list.forEach((p, i) => {
        p.posRank = i + 1;
        if (i > 0 && p.adp - list[i - 1].adp > Math.max(5, 0.085 * p.adp)) tier++;
        p.tier = tier;
      });
    });

    // replacement level per position, from projections
    const repl = {};
    Object.entries(byPos).forEach(([pos, list]) => {
      const sorted = [...list].sort((a, b) => b.projPts - a.projPts);
      const r = teams * ((roster[pos] || 0) + (roster.FLEX || 0) * (FLEX_SHARE[pos] || 0));
      const idx = Math.min(sorted.length - 1, Math.max(0, Math.round(r) - 1));
      repl[pos] = sorted[idx]?.projPts ?? 0;
    });
    rows.forEach((p) => (p.vorp = Math.max(0, p.projPts - (repl[p.pos] ?? 0))));

    // cross-positional value rank + edge vs ADP.
    // K/DST are excluded: their VORP is near-flat, so a VORP-vs-ADP "edge" is
    // noise, not a signal. Elite-QB edge still reads high in 1QB leagues by pure
    // VORP; that's replaceability the market prices correctly, so lean on the
    // tier + "lasts to" columns for QB timing rather than edge alone.
    const rankable = rows.filter((p) => p.pos !== "K" && p.pos !== "DST");
    [...rankable].sort((a, b) => b.vorp - a.vorp).forEach((p, i) => (p.valueRank = i + 1));
    [...rankable].sort((a, b) => a.adp - b.adp).forEach((p, i) => (p.skillAdpRank = i + 1));
    // Edge only means something with an independent projection. Before you sync,
    // "projPts" is just ADP transformed, so comparing it back to ADP is circular
    // and manufactures phantom edges (tight ends especially). Suppress it.
    const projLoaded = Object.keys(proj).length > 0;
    rows.forEach((p) => (p.edge = projLoaded && p.valueRank != null ? p.skillAdpRank - p.valueRank : null));

    // risk tags from what we actually know
    rows.forEach((p) => {
      const tags = [];
      if (p.note && /acl|hamstring|ankle|pup|mcl|sprain|surgery|\bout\b|injur/i.test(p.note)) tags.push("injury");
      if (p.note && /rookie/i.test(p.note)) tags.push("rookie");
      if (p.real && p.tdShare != null && p.tdShare > 0.42) tags.push("TD-reliant");
      p.risk = tags;
      // modeled floor/ceiling band (heuristic, not sourced)
      let v = { QB: 0.15, RB: 0.22, WR: 0.24, TE: 0.28, K: 0.3, DST: 0.34 }[p.pos] ?? 0.24;
      if (tags.includes("rookie")) v += 0.08;
      if (tags.includes("injury")) v += 0.06;
      if (tags.includes("TD-reliant")) v += 0.06;
      if (p.touches && p.touches > 250) v -= 0.04;
      if (p.targets && p.targets > 120) v -= 0.04;
      p.floor = p.projPts * (1 - v);
      p.ceiling = p.projPts * (1 + v);
    });

    return rows;
  }, [source, scoring, proj, teams, roster]);

  const byId = useMemo(() => Object.fromEntries(enriched.map((p) => [p.id, p])), [enriched]);
  const available = useMemo(() => enriched.filter((p) => !taken[p.id]), [enriched, taken]);
  const myTeam = useMemo(
    () => order.filter((id) => taken[id] === "me").map((id) => byId[id]).filter(Boolean),
    [order, taken, byId]
  );

  /* ---- roster ownership + league rosters ---- */
  const ownerOf = useCallback((id) => {
    if (owners[id]) return owners[id];
    const idx = order.indexOf(id);
    return idx >= 0 ? teamAtPick(idx + 1) : null;
  }, [owners, order, teamAtPick]);

  const leagueRosters = useMemo(() => {
    const out = Array.from({ length: teams }, () => []);
    order.forEach((id) => {
      const t = ownerOf(id), pl = byId[id];
      if (t && pl && t <= teams) out[t - 1].push(pl);
    });
    return out;
  }, [order, ownerOf, byId, teams]);

  const needsOf = useCallback((list) => {
    const c = {}; list.forEach((p) => (c[p.pos] = (c[p.pos] || 0) + 1));
    const n = {};
    ["QB", "RB", "WR", "TE", "K", "DST"].forEach((pos) => (n[pos] = Math.max(0, (roster[pos] || 0) - (c[pos] || 0))));
    const spare = FLEX_OK.reduce((a, x) => a + Math.max(0, (c[x] || 0) - (roster[x] || 0)), 0);
    const flexOpen = Math.max(0, (roster.FLEX || 0) - spare);
    FLEX_OK.forEach((x) => (n[x] += flexOpen * 0.4));
    return n;
  }, [roster]);

  const myNeed = useMemo(() => needsOf(myTeam), [myTeam, needsOf]);

  /* ---- 4. SURVIVAL: opponent-need-aware, ADP-prior tilted by who's picking ---- */
  const survival = useMemo(() => {
    const pool = enriched.filter((p) => !taken[p.id]);
    const alive = new Map(pool.map((p) => [p.id, 1]));
    const snapNext = new Map(), snapAfter = new Map();
    const needs = leagueRosters.map((r) => needsOf(r));
    const target = afterNext ?? nextMine ?? currentPick;
    const S = (p, k) => Math.max(1e-9, 1 - normCdf((k - p.adp) / p.sdv));

    for (let k = currentPick; k < target; k++) {
      const t = teamAtPick(k), tn = needs[t - 1] || {}, round = Math.floor((k - 1) / teams) + 1;
      const hz = []; let tot = 0;
      for (const p of pool) {
        const a = alive.get(p.id);
        if (a < 0.002) { hz.push(0); continue; }
        const s0 = S(p, k - 1);
        let base = Math.max(0, Math.min(0.95, (s0 - S(p, k)) / s0));
        const overdue = (k - p.adp) / p.sdv;
        if (overdue > 0) base = Math.max(base, Math.min(0.9, 0.25 + 0.25 * overdue));
        let m;
        if ((p.pos === "K" || p.pos === "DST") && round < rounds - 2) m = 0.04;
        else if ((tn[p.pos] || 0) > 0) m = 1 + 1.6 * Math.min(2, tn[p.pos]);
        else m = 0.5;
        const h = base * m; hz.push(h); tot += a * h;
      }
      if (tot > 0) {
        const lam = 1 / tot, posTake = {};
        pool.forEach((p, i) => {
          if (!hz[i]) return;
          const h = Math.min(0.95, hz[i] * lam);
          alive.set(p.id, alive.get(p.id) * (1 - h));
          posTake[p.pos] = (posTake[p.pos] || 0) + h;
        });
        Object.entries(posTake).forEach(([pos, amt]) => {
          if (needs[t - 1]) needs[t - 1][pos] = Math.max(0, (needs[t - 1][pos] || 0) - amt);
        });
      }
      if (k + 1 === nextMine) pool.forEach((p) => snapNext.set(p.id, alive.get(p.id)));
    }
    pool.forEach((p) => {
      snapAfter.set(p.id, alive.get(p.id));
      if (!snapNext.has(p.id)) snapNext.set(p.id, alive.get(p.id));
    });
    return { atNext: snapNext, atAfter: snapAfter };
  }, [enriched, taken, leagueRosters, needsOf, currentPick, nextMine, afterNext, teamAtPick, teams, rounds]);

  const surviveAdp = useCallback((p, pick) => {
    if (!pick) return 0;
    const now = Math.max(1, currentPick - 1);
    const pNow = 1 - normCdf((now - p.adp) / p.sdv);
    const pThen = 1 - normCdf((pick - 0.5 - p.adp) / p.sdv);
    if (pNow <= 0.02) return Math.max(0, Math.min(1, pThen));
    return Math.max(0, Math.min(1, pThen / pNow));
  }, [currentPick]);

  /* ---- run detector ---- */
  const run = useMemo(() => {
    const last = order.slice(-8).map((id) => byId[id]).filter(Boolean);
    if (last.length < 5) return null;
    const c = {}; last.forEach((p) => (c[p.pos] = (c[p.pos] || 0) + 1));
    const [pos, n] = Object.entries(c).sort((a, b) => b[1] - a[1])[0];
    return n >= 5 ? { pos, n, of: last.length } : null;
  }, [order, byId]);

  /* ---- recommendation score (balanced: value edge + urgency + need + scarcity) ---- */
  const scored = useMemo(() => {
    const tierLeft = {};
    available.forEach((p) => (tierLeft[p.pos + "|" + p.tier] = (tierLeft[p.pos + "|" + p.tier] || 0) + 1));
    return available.map((p) => {
      const valueEdge = Math.max(-2, Math.min(2, (p.edge ?? 0) / 12)); // spots of market value
      const survive = survival.atAfter.get(p.id) ?? surviveAdp(p, afterNext ?? nextMine);
      const survNext = survival.atNext.get(p.id) ?? survive;
      const survAdp = surviveAdp(p, afterNext ?? nextMine);
      const urgency = 1 - survive;   // P(gone before your next pick)
      const scarce = 1 / Math.sqrt(tierLeft[p.pos + "|" + p.tier] || 1);
      const needW = Math.min(1.5, myNeed[p.pos] ?? 0);
      const riskPen = (p.risk?.length || 0) * 0.15;
      // Value only matters to the extent he WON'T keep. A player near-certain to
      // last is a "wait", not a "take now", however strong his edge — otherwise
      // the list fills with bargains you can grab 8 rounds later.
      const takeNow = 0.25 + 0.75 * urgency;
      const score =
          valueEdge * 1.1 * takeNow
        + needW * (0.4 + 0.6 * urgency) * 0.9
        + scarce * urgency * 0.9
        + urgency * 0.4
        - riskPen
        - (p.pos === "K" || p.pos === "DST" ? 1.6 : 0);
      return { ...p, valueEdge, survive, survNext, survAdp, urgency, scarce, needW, score,
               tierLeft: tierLeft[p.pos + "|" + p.tier] };
    });
  }, [available, survival, surviveAdp, afterNext, nextMine, myNeed]);

  const recs = useMemo(() => [...scored].sort((a, b) => b.score - a.score).slice(0, 6), [scored]);

  const shown = useMemo(() => {
    let list = scored;
    if (posFilter !== "ALL")
      list = list.filter((p) => (posFilter === "FLEX" ? FLEX_OK.includes(p.pos) : p.pos === posFilter));
    if (query.trim()) {
      const q = query.toLowerCase();
      list = list.filter((p) => p.name.toLowerCase().includes(q) || p.team.toLowerCase() === q);
    }
    return [...list].sort((a, b) => a.adp - b.adp).slice(0, 100);
  }, [scored, posFilter, query]);

  /* ---- auction ---- */
  const priced = useMemo(() => {
    const drafted = teams * rounds, pool = teams * budget - drafted;
    const tot = [...enriched].sort((a, b) => b.vorp - a.vorp).slice(0, drafted).reduce((a, b) => a + b.vorp, 0);
    const m = {};
    enriched.forEach((p) => (m[p.id] = tot > 0 ? 1 + (p.vorp / tot) * pool : 1));
    return m;
  }, [enriched, teams, rounds, budget]);

  const auction = useMemo(() => {
    const soldIds = Object.keys(prices).map(Number);
    const spentLeague = soldIds.reduce((a, id) => a + (prices[id] || 0), 0);
    const mySpent = soldIds.filter((id) => taken[id] === "me").reduce((a, id) => a + prices[id], 0);
    const mySlots = rounds - myTeam.length, myLeft = budget - mySpent;
    const maxBid = Math.max(0, myLeft - Math.max(0, mySlots - 1));
    const slotsLeft = teams * rounds - soldIds.length;
    const moneyLeft = teams * budget - spentLeague;
    const remainValue = enriched.filter((p) => !taken[p.id])
      .sort((a, b) => priced[b.id] - priced[a.id]).slice(0, Math.max(1, slotsLeft))
      .reduce((a, b) => a + priced[b.id], 0);
    const inflation = remainValue > 0 ? moneyLeft / remainValue : 1;
    return { mySpent, myLeft, mySlots, maxBid, moneyLeft, slotsLeft, inflation };
  }, [prices, taken, budget, teams, rounds, myTeam.length, enriched, priced]);

  /* ---- actions ---- */
  const pick = (id, who) => { setTaken((t) => ({ ...t, [id]: who })); setOrder((o) => [...o, id]); };
  const logSale = (id, price, buyer) => {
    setPrices((pr) => ({ ...pr, [id]: price }));
    setTaken((t) => ({ ...t, [id]: buyer === slot ? "me" : "them" }));
    setOwners((ow) => ({ ...ow, [id]: buyer }));
    setOrder((o) => [...o, id]); setBidPlayer(null); setBidAmt("");
  };
  const undo = () => {
    if (!order.length) return;
    const last = order[order.length - 1];
    setOrder((o) => o.slice(0, -1));
    setTaken((t) => { const n = { ...t }; delete n[last]; return n; });
    setOwners((o) => { const n = { ...o }; delete n[last]; return n; });
    setPrices((pr) => { const n = { ...pr }; delete n[last]; return n; });
  };
  const reset = () => { setTaken({}); setOrder([]); setOwners({}); setPrices({}); };

  /* ---- projections sync ---- */
  const loadProjections = async () => {
    setProjState({ status: "working", msg: "Pulling projections from Sleeper…" });
    try {
      const map = await fetchSleeperProjections(scoring);
      const matched = enriched.filter((p) => map[p.pos === "DST" ? "dst|" + p.team.toLowerCase() : norm(p.name)]).length;
      setProj(map);
      setProjState({ status: "ok", msg: `Loaded projections — matched ${matched} of your ${PLAYERS.length} players.` });
    } catch (e) {
      setProjState({ status: "error", msg: "Sleeper projections weren't reachable from here. The board is running on ADP-implied values; try again or use the paste import." });
    }
  };

  const importProjCsv = (text) => {
    setProjState({ status: "working", msg: "Reading CSV…" });
    try {
      const { map, count, found } = projFromCsv(text, scoring);
      const cols = Object.entries(found).filter(([, v]) => v).map(([k]) => k);
      const matched = enriched.filter((p) => map[p.pos === "DST" ? "dst|" + p.team.toLowerCase() : norm(p.name)]).length;
      setProj(map);
      setProjState({ status: "ok", msg: `Imported ${count} rows · matched ${matched} of your players · columns: ${cols.join(", ")}.` });
    } catch (e) {
      setProjState({ status: "error", msg: e.message || "Couldn't parse that CSV." });
    }
  };

  /* ---- sleeper draft sync ---- */
  const nameIndex = useMemo(() => {
    const m = {};
    enriched.forEach((p) => {
      m[norm(p.name) + "|" + p.pos] = p.id; m[norm(p.name)] = p.id;
      if (p.pos === "DST") m["dst|" + p.team.toLowerCase()] = p.id;
    });
    return m;
  }, [enriched]);

  const findLeagues = async () => {
    setSyncState({ status: "working", msg: "Looking up your leagues…" });
    try {
      const u = await fetch(`https://api.sleeper.app/v1/user/${encodeURIComponent(sleeperUser.trim())}`);
      if (!u.ok) throw new Error("No Sleeper user with that name.");
      const user = await u.json(); setSleeperId(user.user_id);
      const l = await fetch(`https://api.sleeper.app/v1/user/${user.user_id}/leagues/nfl/2026`);
      const ls = await l.json();
      if (!ls.length) throw new Error("That account has no 2026 NFL leagues.");
      const withDrafts = await Promise.all(ls.map(async (lg) => {
        const d = await fetch(`https://api.sleeper.app/v1/league/${lg.league_id}/drafts`);
        return { name: lg.name, teams: lg.total_rosters, draft: (await d.json())[0] };
      }));
      setLeagues(withDrafts.filter((x) => x.draft));
      setSyncState({ status: "ok", msg: `Found ${withDrafts.length} league(s). Pick one.` });
    } catch (e) {
      setSyncState({ status: "error", msg: /Failed to fetch/.test(e.message) ? "Couldn't reach Sleeper from this page. Use paste import below." : e.message });
    }
  };

  const pullPicks = useCallback(async () => {
    if (!draftId) return;
    try {
      const picks = await (await fetch(`https://api.sleeper.app/v1/draft/${draftId}/picks`)).json();
      if (picks.length === syncedCount.current) return; // nothing new — skip the re-render
      syncedCount.current = picks.length;
      const t = {}, o = [], pr = {}, ow = {}; let matched = 0;
      picks.forEach((pk) => {
        const md = pk.metadata || {};
        const key = (md.position || "").toUpperCase() === "DEF"
          ? "dst|" + (md.team || "").toLowerCase()
          : norm(`${md.first_name || ""} ${md.last_name || ""}`);
        const id = nameIndex[key];
        if (id === undefined) return;
        matched++;
        t[id] = pk.picked_by && pk.picked_by === sleeperId ? "me" : "them";
        o.push(id);
        if (pk.draft_slot) ow[id] = pk.draft_slot; // exact team, straight from Sleeper
        if (md.amount) pr[id] = parseInt(md.amount, 10);
      });
      setTaken((prev) => { const m = { ...t }; Object.entries(prev).forEach(([id, w]) => { if (w === "me" && m[id]) m[id] = "me"; }); return m; });
      setOrder(o);
      setOwners((prev) => ({ ...prev, ...ow }));
      if (Object.keys(pr).length) setPrices((p) => ({ ...p, ...pr }));
      setSyncState({ status: "ok", msg: `Synced ${picks.length} picks (${matched} matched).` });
    } catch (e) { setSyncState({ status: "error", msg: "Sleeper request failed — falling back to manual." }); }
  }, [draftId, nameIndex, sleeperId]);

  useEffect(() => {
    if (!autoSync || !draftId) return;
    syncedCount.current = -1; // force a fresh pull when sync is switched on
    pullPicks();
    const t = setInterval(pullPicks, 4000);
    return () => clearInterval(t);
  }, [autoSync, draftId, pullPicks]);

  const importPasted = (text) => {
    const lines = text.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean);
    let hit = 0; const t = {}, o = [];
    lines.forEach((ln) => { const id = nameIndex[norm(ln)]; if (id !== undefined) { t[id] = "them"; o.push(id); hit++; } });
    setTaken((prev) => ({ ...t, ...Object.fromEntries(Object.entries(prev).filter(([, w]) => w === "me")) }));
    setOrder(o);
    setSyncState({ status: "ok", msg: `Matched ${hit} of ${lines.length} pasted names.` });
  };

  const byeLoad = useMemo(() => {
    const m = {}; myTeam.forEach((p) => (m[p.bye] = (m[p.bye] || 0) + 1)); return m;
  }, [myTeam]);

  /* lineup slots (Yahoo/Sleeper style): starters from roster settings, then bench
     sized to fill the roster to the round count, then K/DEF. Fills as you draft. */
  const lineup = useMemo(() => {
    const pool = [...myTeam];
    const take = (pos) => { const i = pool.findIndex((p) => p.pos === pos); return i >= 0 ? pool.splice(i, 1)[0] : null; };
    const takeFlex = () => { const i = pool.findIndex((p) => FLEX_OK.includes(p.pos)); return i >= 0 ? pool.splice(i, 1)[0] : null; };
    const rows = [];
    const add = (label, n, fn) => { for (let i = 0; i < (n || 0); i++) rows.push({ label, player: fn() }); };
    add("QB", roster.QB, () => take("QB"));
    add("RB", roster.RB, () => take("RB"));
    add("WR", roster.WR, () => take("WR"));
    add("TE", roster.TE, () => take("TE"));
    add("FLEX", roster.FLEX, takeFlex);
    // claim K/DEF players before bench so they don't fall into a bench slot
    const kP = []; for (let i = 0; i < (roster.K || 0); i++) kP.push(take("K"));
    const dP = []; for (let i = 0; i < (roster.DST || 0); i++) dP.push(take("DST"));
    const starters = ["QB", "RB", "WR", "TE", "FLEX", "K", "DST"].reduce((a, k) => a + (roster[k] || 0), 0);
    const bench = Math.max(0, rounds - starters);
    for (let i = 0; i < bench; i++) rows.push({ label: "BEN", player: pool.length ? pool.shift() : null });
    kP.forEach((p) => rows.push({ label: "K", player: p }));
    dP.forEach((p) => rows.push({ label: "DEF", player: p }));
    return rows;
  }, [myTeam, roster, rounds]);

  /* ===================== render ===================== */
  return (
    <div style={{ background: THEME.bg, color: THEME.chalk, minHeight: "100vh", fontFamily: DISPLAY }}>
      <style>{`
        .wr-grid { grid-template-columns: minmax(0,1fr); }
        @media (min-width:1024px){ .wr-grid { grid-template-columns: minmax(0,1fr) 320px; } }
        .wr-scroll { overflow-x:auto; } .wr-scroll > * { min-width:660px; }
        .wr-row:hover { background:#16231C; }
        *:focus-visible { outline:2px solid ${THEME.signal}; outline-offset:2px; }
        @media (prefers-reduced-motion:reduce){ * { transition:none!important; } }
      `}</style>

      {/* header */}
      <div className="sticky top-0 z-20 border-b px-4 py-3" style={{ background: THEME.panel, borderColor: THEME.line }}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-lg font-black uppercase leading-none" style={{ letterSpacing: "0.14em" }}>Draft war room</div>
            <div className="mt-1 text-xs" style={{ color: THEME.muted, fontFamily: MONO }}>
              2026 · {available.length} on board · {hasProj ? "projections live" : "ADP-implied values"}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <div className="text-xs uppercase" style={{ color: THEME.muted, letterSpacing: "0.1em" }}>{onClock ? "On the clock" : "Pick"}</div>
              <div className="text-2xl font-black leading-none" style={{ fontFamily: MONO, color: onClock ? THEME.signal : THEME.chalk }}>{rd(currentPick)}</div>
            </div>
            <div className="text-right">
              <div className="text-xs uppercase" style={{ color: THEME.muted, letterSpacing: "0.1em" }}>Your next</div>
              <div className="text-2xl font-black leading-none" style={{ fontFamily: MONO }}>{nextMine ? rd(nextMine) : "—"}</div>
              <div className="text-xs" style={{ color: THEME.muted, fontFamily: MONO }}>{nextMine ? `${nextMine - currentPick} away` : "done"}</div>
            </div>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs" style={{ fontFamily: MONO }}>
          <Select label="Teams" value={teams} onChange={(v) => setTeams(+v)} options={[8, 10, 12, 14]} />
          <Select label="Slot" value={slot} onChange={(v) => setSlot(+v)} options={Array.from({ length: teams }, (_, i) => i + 1)} />
          <Select label="Rounds" value={rounds} onChange={(v) => setRounds(+v)} options={[13, 14, 15, 16, 17, 18]} />
          <Select label="Scoring" value={scoring} onChange={setScoring} options={["ppr", "half"]} />
          <Select label="ADP" value={source} onChange={setSource} options={SOURCES.map((s) => s.key)} labels={SOURCES.map((s) => s.label)} />
          <button onClick={loadProjections} className="rounded px-2 py-1 font-bold"
                  style={{ background: hasProj ? THEME.panel2 : THEME.good, color: hasProj ? THEME.good : "#0A100D", border: `1px solid ${THEME.line}` }}>
            {hasProj ? "↻ projections" : "sync projections"}
          </button>
          <button onClick={undo} className="rounded px-2 py-1" style={{ background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk }}>Undo</button>
          <button onClick={reset} className="rounded px-2 py-1" style={{ background: "transparent", border: `1px solid ${THEME.line}`, color: THEME.muted }}>Clear</button>
          {saveNote && <span style={{ color: THEME.dim }}>{saveNote}</span>}
        </div>
        {projState.status !== "idle" && (
          <div className="mt-2 text-xs" style={{ color: projState.status === "error" ? THEME.hot : projState.status === "ok" ? THEME.cool : THEME.muted }}>
            {projState.msg}
          </div>
        )}

        <div className="mt-3 flex flex-wrap gap-1 text-xs" style={{ fontFamily: MONO }}>
          {[["board", "Board"], ["auction", "Auction"], ["league", "League"], ["arb", "Platform edges"], ["team", `My team (${myTeam.length})`], ["sync", "Sync"]].map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)} className="rounded-t px-3 py-1.5 font-bold uppercase"
                    style={{ letterSpacing: "0.08em", background: tab === k ? THEME.panel2 : "transparent",
                             color: tab === k ? THEME.signal : THEME.muted, borderBottom: tab === k ? `2px solid ${THEME.signal}` : "2px solid transparent" }}>
              {l}
            </button>
          ))}
        </div>

        {myTeam.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs" style={{ fontFamily: MONO }}>
            <span className="uppercase" style={{ color: THEME.dim, letterSpacing: "0.12em" }}>Your byes</span>
            {Object.entries(byeLoad).sort((a, b) => +a[0] - +b[0]).map(([w, n]) => {
              const clash = n >= 3;
              return (
                <span key={w} className="rounded px-1.5 py-0.5"
                      title={clash ? `${n} of your players are off in week ${w}` : `${n} off in week ${w}`}
                      style={{ background: clash ? THEME.hot + "22" : THEME.panel2, color: clash ? THEME.hot : THEME.muted, border: `1px solid ${clash ? THEME.hot + "55" : THEME.line}` }}>
                  W{w}·{n}
                </span>
              );
            })}
            {Object.values(byeLoad).some((n) => n >= 3) && (
              <span style={{ color: THEME.hot }}>← bye stack, steer around it</span>
            )}
          </div>
        )}
      </div>

      <div className="wr-grid mx-auto grid max-w-7xl gap-4 p-4">
        <div className="min-w-0">
          {run && (
            <div className="mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-xs" style={{ background: POS_COLOR[run.pos] + "1a", border: `1px solid ${POS_COLOR[run.pos]}44` }}>
              <span className="font-black uppercase" style={{ color: POS_COLOR[run.pos], letterSpacing: "0.1em", fontFamily: MONO }}>{run.pos} run</span>
              <span style={{ color: THEME.chalk }}>{run.n} of the last {run.of} picks were {run.pos}. That tier is thinning faster than ADP expects.</span>
            </div>
          )}

          {tab === "board" && (
            <Board shown={shown} posFilter={posFilter} setPosFilter={setPosFilter} query={query} setQuery={setQuery}
                   currentPick={currentPick} afterNext={afterNext} nextMine={nextMine} rd={rd} pick={pick} hasProj={hasProj} />
          )}
          {tab === "auction" && (
            <AuctionRoom enriched={enriched} taken={taken} prices={prices} priced={priced} auction={auction}
                         budget={budget} setBudget={setBudget} logSale={logSale} bidPlayer={bidPlayer} setBidPlayer={setBidPlayer}
                         bidAmt={bidAmt} setBidAmt={setBidAmt} teams={teams} slot={slot} teamNames={teamNames} hasProj={hasProj}
                         owners={owners} roster={roster} rounds={rounds} needsOf={needsOf} myTeam={myTeam} />
          )}
          {tab === "league" && (
            <LeagueBoard rosters={leagueRosters} needsOf={needsOf} slot={slot} teamNames={teamNames} setTeamNames={setTeamNames}
                         teamAtPick={teamAtPick} currentPick={currentPick} rounds={rounds} />
          )}
          {tab === "arb" && <Arbitrage players={available} scoring={scoring} teams={teams} />}
          {tab === "team" && <MyTeam team={myTeam} roster={roster} setRoster={setRoster} byeLoad={byeLoad} available={available} pick={pick} />}
          {tab === "sync" && (
            <SyncPanel sleeperUser={sleeperUser} setSleeperUser={setSleeperUser} findLeagues={findLeagues} leagues={leagues}
                       draftId={draftId} setDraftId={setDraftId} pullPicks={pullPicks} autoSync={autoSync} setAutoSync={setAutoSync}
                       syncState={syncState} importPasted={importPasted} setTeams={setTeams} setRounds={setRounds} setBudget={setBudget}
                       loadProjections={loadProjections} importProjCsv={importProjCsv} hasProj={hasProj} />
          )}
        </div>

        {/* right rail */}
        <div className="space-y-3">
          <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
            <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.signal, letterSpacing: "0.12em", fontFamily: MONO }}>Take one of these</div>
            {recs.map((p, i) => (
              <div key={p.id} className="mb-2 border-b pb-2 last:border-0" style={{ borderColor: THEME.line }}>
                <div className="flex items-baseline justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <span className="text-xs font-bold" style={{ color: THEME.dim, fontFamily: MONO }}>{i + 1}</span>
                    <PosTag pos={p.pos} rank={p.posRank} />
                    <span className="truncate text-sm font-semibold">{p.name}</span>
                  </div>
                  <button onClick={() => pick(p.id, "me")} className="shrink-0 rounded px-1.5 py-0.5 text-xs font-bold" style={{ background: THEME.signal, color: "#141007" }}>+</button>
                </div>
                <div className="mt-1 flex flex-wrap gap-1 text-xs" style={{ fontFamily: MONO, color: THEME.muted }}>
                  <Chip on={p.edge > 3} label={`edge ${p.edge > 0 ? "+" : ""}${p.edge ?? 0}`} />
                  <Chip on={p.urgency > 0.6} label={`${Math.round(p.survive * 100)}% lasts`} />
                  <Chip on={p.tierLeft <= 2} label={`${p.tierLeft} in tier`} />
                  {Math.abs(p.survive - p.survAdp) > 0.12 && <Chip on label={p.survive > p.survAdp ? "board: wait" : "board: now"} />}
                </div>
              </div>
            ))}
          </div>

          <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
            <div className="mb-2 flex items-baseline justify-between">
              <div className="text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>My roster</div>
              <div className="text-xs" style={{ fontFamily: MONO, color: THEME.dim }}>{myTeam.length}/{rounds}</div>
            </div>
            {myTeam.length === 0 ? (
              <div className="space-y-0.5">
                {lineup.map((s, i) => (
                  <div key={i} className="flex items-center gap-2 py-0.5 text-xs">
                    <span className="w-9 shrink-0 font-bold" style={{ color: THEME.dim, fontFamily: MONO }}>{s.label}</span>
                    <span className="flex-1" style={{ color: THEME.dim }}>empty</span>
                  </div>
                ))}
              </div>
            ) : (
              <>
                <div className="space-y-0.5">
                  {lineup.map((s, i) => {
                    const labelColor = POS_COLOR[s.label] || (s.label === "DEF" ? POS_COLOR.DST : THEME.muted);
                    const clash = s.player && (byeLoad[s.player.bye] || 0) >= 3;
                    return (
                      <div key={i} className="flex items-center gap-2 py-0.5 text-xs">
                        <span className="w-9 shrink-0 font-bold" style={{ color: s.player ? labelColor : THEME.dim, fontFamily: MONO }}>{s.label}</span>
                        {s.player ? (
                          <>
                            <span className="min-w-0 flex-1 truncate">{s.player.name}</span>
                            <span className="shrink-0 rounded px-1 py-0.5" style={{ fontFamily: MONO, background: clash ? THEME.hot + "22" : THEME.panel2, color: clash ? THEME.hot : THEME.muted }}>b{s.player.bye}</span>
                          </>
                        ) : (
                          <span className="flex-1" style={{ color: THEME.dim }}>empty</span>
                        )}
                      </div>
                    );
                  })}
                </div>
                {Object.entries(byeLoad).filter(([, n]) => n >= 3).length > 0 && (
                  <p className="mt-2 text-xs" style={{ color: THEME.hot }}>
                    Bye stack — {Object.entries(byeLoad).filter(([, n]) => n >= 3).map(([w, n]) => `W${w} (${n})`).join(", ")}. Steer around it from here.
                  </p>
                )}
              </>
            )}
          </div>

          <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
            <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Tier cliffs</div>
            {["RB", "WR", "TE", "QB"].map((pos) => {
              const list = available.filter((p) => p.pos === pos).sort((a, b) => a.adp - b.adp);
              if (!list.length) return null;
              const top = list[0], left = list.filter((p) => p.tier === top.tier).length;
              return (
                <div key={pos} className="mb-2">
                  <div className="flex items-center justify-between text-xs" style={{ fontFamily: MONO }}>
                    <span style={{ color: POS_COLOR[pos] }}>{pos} tier {top.tier}</span>
                    <span style={{ color: left <= 2 ? THEME.hot : THEME.muted }}>{left} left</span>
                  </div>
                  <div className="truncate text-xs" style={{ color: THEME.dim }}>
                    {list.filter((p) => p.tier === top.tier).slice(0, 4).map((p) => p.name.split(" ").slice(-1)[0]).join(", ")}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="rounded-lg p-3 text-xs" style={{ background: THEME.panel, border: `1px solid ${THEME.line}`, color: THEME.muted }}>
            <div className="mb-1 font-black uppercase" style={{ letterSpacing: "0.12em", fontFamily: MONO }}>Your picks</div>
            <div className="flex flex-wrap gap-1" style={{ fontFamily: MONO }}>
              {myPicks.map((p) => (
                <span key={p} className="rounded px-1.5 py-0.5" style={{
                  background: p < currentPick ? "transparent" : p === nextMine ? THEME.signal + "22" : THEME.panel2,
                  color: p < currentPick ? THEME.dim : p === nextMine ? THEME.signal : THEME.muted,
                  textDecoration: p < currentPick ? "line-through" : "none" }}>{rd(p)}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ==========================================================================
   BOARD  — now shows projection, VORP, edge, opportunity, risk
   ========================================================================== */
function Board({ shown, posFilter, setPosFilter, query, setQuery, currentPick, afterNext, nextMine, rd, pick, hasProj }) {
  const opp = (p) => {
    if (p.pos === "RB" && p.touches != null) return `${Math.round(p.touches)} tch`;
    if ((p.pos === "WR" || p.pos === "TE") && p.targets != null) return `${Math.round(p.targets)} tgt`;
    return "";
  };
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {["ALL", "QB", "RB", "WR", "TE", "FLEX", "K", "DST"].map((p) => (
          <button key={p} onClick={() => setPosFilter(p)} className="rounded px-2.5 py-1 text-xs font-bold"
                  style={{ fontFamily: MONO, background: posFilter === p ? (POS_COLOR[p] || THEME.signal) + "26" : THEME.panel,
                           color: posFilter === p ? POS_COLOR[p] || THEME.signal : THEME.muted,
                           border: `1px solid ${posFilter === p ? (POS_COLOR[p] || THEME.signal) + "55" : THEME.line}` }}>{p}</button>
        ))}
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search player or team"
               className="ml-auto w-48 rounded px-2 py-1 text-xs outline-none"
               style={{ fontFamily: MONO, background: THEME.panel, border: `1px solid ${THEME.line}`, color: THEME.chalk }} />
      </div>

      <div className="wr-scroll rounded-lg" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div>
          <div className="grid items-center gap-2 px-3 py-2 text-xs font-bold uppercase"
               style={{ gridTemplateColumns: "2.1fr 46px 52px 52px 54px 64px 84px 60px", color: THEME.dim, fontFamily: MONO, letterSpacing: "0.06em", borderBottom: `1px solid ${THEME.line}` }}>
            <div>Player</div><div>Tier</div><div>ADP</div><div>Proj</div><div>Edge</div><div>Opp</div>
            <div>Lasts to {afterNext ? rd(afterNext) : "—"}</div><div />
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: "62vh" }}>
            {shown.map((p) => {
              const delta = p.adp - currentPick;
              return (
                <div key={p.id} className="wr-row grid items-center gap-2 px-3 py-2"
                     style={{ gridTemplateColumns: "2.1fr 46px 52px 52px 54px 64px 84px 60px", borderBottom: `1px solid ${THEME.line}55` }}>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <PosTag pos={p.pos} rank={p.posRank} />
                      <span className="truncate text-sm font-semibold">{p.name}</span>
                      <span className="text-xs" style={{ color: THEME.dim, fontFamily: MONO }}>{p.team}·b{p.bye}</span>
                      {p.risk?.map((r) => (
                        <span key={r} className="rounded px-1 text-xs" style={{ fontFamily: MONO, color: r === "TD-reliant" ? THEME.hot : THEME.muted, border: `1px solid ${THEME.line}` }}>{r}</span>
                      ))}
                    </div>
                    {p.note && <div className="mt-0.5 truncate text-xs" style={{ color: THEME.muted }}>{p.note}</div>}
                  </div>
                  <div className="text-xs" style={{ fontFamily: MONO, color: THEME.muted }}>T{p.tier}<span style={{ color: THEME.dim }}>·{p.tierLeft}</span></div>
                  <div className="text-sm font-bold" style={{ fontFamily: MONO }}>{p.adp.toFixed(1)}</div>
                  <div className="text-sm" style={{ fontFamily: MONO, color: p.real ? THEME.chalk : THEME.dim }}>{p.real ? Math.round(p.projPts) : "—"}</div>
                  <div className="text-sm font-bold" style={{ fontFamily: MONO, color: p.edge == null ? THEME.dim : p.edge > 6 ? THEME.good : p.edge < -6 ? THEME.hot : THEME.muted }}>{p.edge == null ? "—" : `${p.edge > 0 ? "+" : ""}${p.edge}`}</div>
                  <div className="text-xs" style={{ fontFamily: MONO, color: THEME.muted }}>{opp(p)}</div>
                  <div>
                    <Bar value={p.survive} color={p.survive > 0.66 ? THEME.cool : p.survive > 0.33 ? THEME.signal : THEME.hot} />
                    <div className="mt-0.5 flex items-baseline gap-1 text-xs" style={{ fontFamily: MONO }}>
                      <span style={{ color: THEME.chalk }}>{Math.round(p.survive * 100)}%</span>
                      <span style={{ color: THEME.dim }}>adp {Math.round(p.survAdp * 100)}</span>
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button onClick={() => pick(p.id, "me")} className="rounded px-2 py-1 text-xs font-bold" style={{ background: THEME.signal, color: "#141007" }} title="Draft to my team">MINE</button>
                    <button onClick={() => pick(p.id, "them")} className="rounded px-2 py-1 text-xs" style={{ background: THEME.panel2, color: THEME.muted, border: `1px solid ${THEME.line}` }} title="Someone else took him">✕</button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <p className="mt-2 text-xs" style={{ color: THEME.dim }}>
        <b style={{ color: THEME.muted }}>Proj</b> is Sleeper's projected points ({hasProj ? "live" : "ADP-implied until you sync"}).
        <b style={{ color: THEME.muted }}> Edge</b> is where projections value him minus where ADP drafts him — positive means the market is underpricing him.
        <b style={{ color: THEME.muted }}> Opp</b> is projected targets (WR/TE) or touches (RB), the volume behind the number.
        <b style={{ color: THEME.muted }}> Lasts</b> simulates every pick to your next using each team's real needs; the faded figure is plain ADP.
      </p>
    </>
  );
}

/* ==========================================================================
   AUCTION
   ========================================================================== */
function AuctionRoom({ enriched, taken, prices, priced, auction, budget, setBudget, logSale, bidPlayer, setBidPlayer, bidAmt, setBidAmt, teams, slot, teamNames, hasProj, owners, roster, rounds, needsOf, myTeam }) {
  const [q, setQ] = useState(""); const [posF, setPosF] = useState("ALL");
  const [buyer, setBuyer] = useState(slot);
  useEffect(() => setBuyer(slot), [slot]);
  const [phil, setPhil] = useState("balanced");
  const money = (n) => `$${Math.max(1, Math.round(n))}`;
  const inflated = (p) => priced[p.id] * auction.inflation;
  const nameOf = (t) => (t === slot ? "You" : teamNames?.[t] ?? `Team ${t}`);

  const open = useMemo(() => enriched.filter((p) => !taken[p.id])
    .filter((p) => (posF === "ALL" ? true : p.pos === posF))
    .filter((p) => (q.trim() ? p.name.toLowerCase().includes(q.toLowerCase()) : true))
    .sort((a, b) => priced[b.id] - priced[a.id]).slice(0, 80), [enriched, taken, posF, q, priced]);

  const sold = useMemo(() => Object.keys(prices).map(Number)
    .map((id) => { const p = enriched.find((x) => x.id === id); return p && { ...p, paid: prices[id], mine: taken[id] === "me", list: priced[id] }; })
    .filter(Boolean).reverse().slice(0, 12), [prices, enriched, taken, priced]);

  // per-team budget + needs summary (the "track the board" principle)
  const summary = useMemo(() => {
    const arr = [];
    for (let t = 1; t <= teams; t++) {
      const ids = Object.keys(owners).map(Number).filter((id) => owners[id] === t && taken[id]);
      const spent = ids.reduce((a, id) => a + (prices[id] || 0), 0);
      const roster_ = ids.map((id) => enriched.find((p) => p.id === id)).filter(Boolean);
      const openSlots = Math.max(0, rounds - roster_.length);
      const left = budget - spent;
      const maxBid = Math.max(0, left - Math.max(0, openSlots - 1));
      arr.push({ t, spent, left, filled: roster_.length, openSlots, maxBid, needs: needsOf(roster_) });
    }
    return arr;
  }, [owners, prices, taken, enriched, teams, rounds, budget, needsOf]);

  const myNeed = useMemo(() => needsOf(myTeam), [myTeam, needsOf]);
  const canOutbid = (price) => summary.filter((s) => s.t !== slot && s.maxBid >= price).length;
  const leagueSpent = summary.reduce((a, s) => a + s.spent, 0);
  const phase = leagueSpent / (teams * budget || 1);

  // nomination helper: early -> drain rivals on players you don't need; late -> force cash-rich teams to spend
  const nominations = useMemo(() => {
    const avail = enriched.filter((p) => !taken[p.id]);
    if (phase >= 0.7) {
      return avail.map((p) => ({ ...p, par: inflated(p), why: "cheap filler" })).filter((x) => x.par <= 5).sort((a, b) => a.par - b.par).slice(0, 4);
    }
    const scored = avail.map((p) => {
      const par = inflated(p);
      const oppNeed = summary.filter((s) => s.t !== slot && (s.needs[p.pos] || 0) > 0 && s.maxBid >= par * 0.6).length;
      const iNeed = (myNeed[p.pos] || 0) > 0;
      return { ...p, par, oppNeed, iNeed };
    });
    const needBased = scored.filter((x) => !x.iNeed && x.par >= 12 && x.oppNeed >= 1)
      .sort((a, b) => b.oppNeed * b.par - a.oppNeed * a.par)
      .map((x) => ({ ...x, why: `${x.oppNeed} rivals need ${x.pos}` }));
    // fallback so it's useful from pick 1: priciest names draw a big bid from someone
    const topPriced = [...scored].sort((a, b) => b.par - a.par).map((x) => ({ ...x, why: "top $ — someone overpays" }));
    const seen = new Set(needBased.map((x) => x.id));
    return [...needBased, ...topPriced.filter((x) => !seen.has(x.id))].slice(0, 4);
  }, [enriched, taken, summary, myNeed, phase, priced, auction.inflation, slot]);

  // budget plan from philosophy
  const topN = phil === "stars" ? 4 : 3;
  const topBudget = Math.round(budget * (phil === "stars" ? 0.66 : 0.5));
  const restPerSlot = Math.max(1, Math.round((budget - topBudget) / Math.max(1, rounds - topN)));
  const myBought = Object.keys(prices).map(Number).filter((id) => taken[id] === "me").map((id) => prices[id]).sort((a, b) => b - a);
  const topSpent = myBought.slice(0, topN).reduce((a, b) => a + b, 0);
  const avgLeft = auction.myLeft / Math.max(1, auction.mySlots);
  const baseAvg = budget / rounds;
  const pace = auction.mySlots <= 0 ? "roster full"
    : avgLeft > baseAvg * 1.4 ? "banking cash — you can afford a stud"
    : avgLeft < 2.5 ? "tight — mostly $1 fills from here"
    : "on pace";
  const paceColor = pace.startsWith("banking") ? THEME.cool : pace.startsWith("tight") ? THEME.hot : THEME.muted;

  return (
    <div className="space-y-3">
      {/* budget stats */}
      <div className="grid gap-3 rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}`, gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))" }}>
        <Stat label="Budget left" value={money(auction.myLeft)} big />
        <Stat label="Max bid now" value={money(auction.maxBid)} big color={THEME.signal} />
        <Stat label="Slots to fill" value={auction.mySlots} />
        <Stat label="Avg $/slot left" value={money(avgLeft)} color={paceColor} />
        <Stat label="Room inflation" value={`${(auction.inflation * 100 - 100).toFixed(0)}%`}
              color={auction.inflation > 1.08 ? THEME.hot : auction.inflation < 0.92 ? THEME.cool : THEME.muted} />
      </div>

      {/* budget plan */}
      <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-xs font-black uppercase" style={{ color: THEME.signal, letterSpacing: "0.12em", fontFamily: MONO }}>Budget plan</span>
          <div className="flex gap-1">
            {[["balanced", "Balanced"], ["stars", "Stars & scrubs"]].map(([k, l]) => (
              <button key={k} onClick={() => setPhil(k)} className="rounded px-2 py-0.5 text-xs font-bold"
                      style={{ fontFamily: MONO, background: phil === k ? THEME.signal + "26" : "transparent", color: phil === k ? THEME.signal : THEME.muted, border: `1px solid ${THEME.line}` }}>{l}</button>
            ))}
          </div>
          <span className="ml-auto text-xs font-bold" style={{ fontFamily: MONO, color: paceColor }}>{pace}</span>
        </div>
        <p className="text-xs" style={{ color: THEME.muted }}>
          {phil === "stars"
            ? `Spend up to about ${money(topBudget)} on your top ${topN} studs (~two-thirds of budget), then fill the rest at roughly ${money(restPerSlot)}/slot — high ceiling, but a hurt star sinks you.`
            : `Put about ${money(topBudget)} across your top ${topN} players (~half the budget) and build a deep middle at roughly ${money(restPerSlot)}/slot — steadier floor.`}
          {" "}You've committed <b style={{ color: THEME.chalk }}>{money(topSpent || 0)}</b> to your top {Math.min(topN, myBought.length)} so far.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-lg p-3 text-xs" style={{ background: THEME.panel, border: `1px solid ${THEME.line}`, fontFamily: MONO, color: THEME.muted }}>
        <label className="flex items-center gap-1">BUDGET
          <input type="number" value={budget} onChange={(e) => setBudget(Math.max(1, +e.target.value))} className="w-16 rounded px-1 py-0.5 outline-none"
                 style={{ background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk }} /></label>
        <span style={{ color: THEME.dim }}>Par $ = VORP value from {hasProj ? "projections" : "ADP-implied"}, scaled by live inflation. It's the expected going price — treat it as your ceiling, not a target.</span>
      </div>

      {/* value board + logger */}
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))" }}>
        <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
          <div className="mb-2 flex flex-wrap items-center gap-1">
            {["ALL", "QB", "RB", "WR", "TE", "K", "DST"].map((x) => (
              <button key={x} onClick={() => setPosF(x)} className="rounded px-2 py-0.5 text-xs font-bold"
                      style={{ fontFamily: MONO, background: posF === x ? (POS_COLOR[x] || THEME.signal) + "26" : "transparent", color: posF === x ? POS_COLOR[x] || THEME.signal : THEME.muted, border: `1px solid ${THEME.line}` }}>{x}</button>
            ))}
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" className="ml-auto w-28 rounded px-2 py-0.5 text-xs outline-none"
                   style={{ fontFamily: MONO, background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk }} />
          </div>
          <div className="flex items-center gap-2 px-1 pb-1 text-xs font-bold uppercase" style={{ color: THEME.dim, fontFamily: MONO, letterSpacing: "0.06em" }}>
            <span className="flex-1">Player</span><span className="w-10 text-right">Par</span><span className="w-12" />
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: "48vh" }}>
            {open.map((p) => {
              const price = inflated(p), afford = price <= auction.maxBid;
              return (
                <div key={p.id} className="flex items-center gap-2 py-1.5" style={{ borderBottom: `1px solid ${THEME.line}55` }}>
                  <PosTag pos={p.pos} rank={p.posRank} />
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold">{p.name}</span>
                  <span className="text-xs" style={{ color: THEME.dim, fontFamily: MONO }}>{p.team}</span>
                  <span className="w-10 text-right text-sm font-bold" style={{ fontFamily: MONO, color: afford ? THEME.chalk : THEME.dim }}>{money(price)}</span>
                  <button onClick={() => { setBidPlayer(p); setBidAmt(String(Math.round(price))); }} className="rounded px-2 py-0.5 text-xs font-bold"
                          style={{ background: THEME.panel2, color: THEME.signal, border: `1px solid ${THEME.line}` }}>sold</button>
                </div>
              );
            })}
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
            <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.signal, letterSpacing: "0.12em", fontFamily: MONO }}>Log a sale</div>
            {bidPlayer ? (
              <>
                <div className="mb-2 flex items-center gap-2">
                  <PosTag pos={bidPlayer.pos} rank={bidPlayer.posRank} />
                  <span className="text-sm font-semibold">{bidPlayer.name}</span>
                  <span className="ml-auto text-xs" style={{ fontFamily: MONO, color: THEME.muted }}>par {money(inflated(bidPlayer))}</span>
                </div>
                <div className="mb-2 text-xs" style={{ color: THEME.dim }}>
                  {canOutbid(inflated(bidPlayer))} of {teams - 1} rivals can still outbid you at par.
                  {canOutbid(inflated(bidPlayer)) === 0 && <span style={{ color: THEME.cool }}> Nobody can — he's yours cheap if you want him.</span>}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span style={{ color: THEME.muted, fontFamily: MONO }}>$</span>
                  <input type="number" value={bidAmt} onChange={(e) => setBidAmt(e.target.value)} autoFocus className="w-20 rounded px-2 py-1 text-sm outline-none"
                         style={{ background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk, fontFamily: MONO }} />
                  <select value={buyer} onChange={(e) => setBuyer(+e.target.value)} className="rounded px-2 py-1 text-xs outline-none"
                          style={{ background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk, fontFamily: MONO }}>
                    {Array.from({ length: teams }, (_, i) => i + 1).map((t) => (
                      <option key={t} value={t} style={{ background: THEME.panel2 }}>{nameOf(t)}</option>
                    ))}
                  </select>
                  <button onClick={() => logSale(bidPlayer.id, Math.max(1, +bidAmt || 1), buyer)} className="rounded px-3 py-1 text-xs font-bold" style={{ background: THEME.signal, color: "#141007" }}>Log sale</button>
                </div>
                {+bidAmt > auction.maxBid && buyer === slot && <p className="mt-2 text-xs" style={{ color: THEME.hot }}>Past your max bid of {money(auction.maxBid)} — you'd strand a slot.</p>}
                {+bidAmt > inflated(bidPlayer) * 1.2 && buyer === slot && +bidAmt <= auction.maxBid && <p className="mt-2 text-xs" style={{ color: THEME.hot }}>That's well over par ({money(inflated(bidPlayer))}) — only if he's a must-have.</p>}
              </>
            ) : <p className="text-xs" style={{ color: THEME.dim }}>Hit "sold" beside a player to record his price. Every sale updates inflation and the opponent board.</p>}
          </div>

          <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
            <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Recent sales <span style={{ color: THEME.dim }}>(paid vs par)</span></div>
            {sold.length === 0 && <p className="text-xs" style={{ color: THEME.dim }}>Nothing sold yet.</p>}
            {sold.map((p) => {
              const diff = p.paid - p.list;
              return (
                <div key={p.id} className="flex items-center gap-2 py-1 text-xs" style={{ fontFamily: MONO }}>
                  <span className="w-1" style={{ color: p.mine ? THEME.signal : "transparent" }}>▸</span>
                  <span className="min-w-0 flex-1 truncate" style={{ fontFamily: DISPLAY, color: p.mine ? THEME.signal : THEME.chalk }}>{p.name}</span>
                  <span>{money(p.paid)}</span>
                  <span style={{ color: diff > 3 ? THEME.hot : diff < -3 ? THEME.cool : THEME.dim, width: 44, textAlign: "right" }}>{diff > 0 ? "+" : ""}{Math.round(diff)}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* opponent board + nomination helper */}
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))" }}>
        <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
          <div className="mb-1 text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Who can still spend</div>
          <p className="mb-2 text-xs" style={{ color: THEME.dim }}>A player's price depends on who can afford him. Teams with the most left are your bidding-war threats.</p>
          <div className="space-y-1">
            {[...summary].sort((a, b) => b.left - a.left).map((s) => {
              const hot = s.needs; const shortPos = ["RB", "WR", "TE", "QB"].filter((p) => (hot[p] || 0) >= 0.9);
              return (
                <div key={s.t} className="flex items-center gap-2 text-xs" style={{ fontFamily: MONO }}>
                  <span className="w-16 shrink-0 truncate" style={{ color: s.t === slot ? THEME.signal : THEME.chalk }}>{nameOf(s.t)}</span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-sm" style={{ background: THEME.line }}>
                    <div className="h-full" style={{ width: `${Math.min(100, (s.spent / budget) * 100)}%`, background: THEME.dim }} />
                  </div>
                  <span className="w-10 text-right" style={{ color: s.left > budget * 0.5 ? THEME.cool : THEME.muted }}>{money(s.left)}</span>
                  <span className="w-14 text-right" style={{ color: THEME.dim }}>max {money(s.maxBid)}</span>
                  <span className="w-16 shrink-0" style={{ color: THEME.dim }}>{shortPos.slice(0, 3).join(",") || "set"}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
          <div className="mb-1 text-xs font-black uppercase" style={{ color: THEME.signal, letterSpacing: "0.12em", fontFamily: MONO }}>
            {phase < 0.7 ? "Nominate to drain" : "Nominate to force spend"}
          </div>
          <p className="mb-2 text-xs" style={{ color: THEME.dim }}>
            {phase < 0.7
              ? "Early game: throw out pricey players you don't need but rivals do. Their money takes the hit; yours stays flexible."
              : "Late game: nominate cheap players to make cash-rich teams burn their last dollars — then steal bench value for $1."}
          </p>
          {nominations.length === 0 ? (
            <p className="text-xs" style={{ color: THEME.dim }}>{phase < 0.7 ? "No clean drain targets right now — nominate your least-wanted expensive guy." : "Start tossing out $1-2 players."}</p>
          ) : nominations.map((p) => (
            <div key={p.id} className="flex items-center gap-2 py-1 text-xs">
              <PosTag pos={p.pos} rank={p.posRank} />
              <span className="min-w-0 flex-1 truncate font-semibold">{p.name}</span>
              <span style={{ fontFamily: MONO, color: THEME.muted }}>{money(p.par)}</span>
              <span className="w-28 shrink-0 truncate text-right" style={{ color: THEME.dim }}>{p.why}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ==========================================================================
   LEAGUE
   ========================================================================== */
function LeagueBoard({ rosters, needsOf, slot, teamNames, setTeamNames, teamAtPick, currentPick, rounds }) {
  const upcoming = [];
  for (let k = currentPick; k < currentPick + 12; k++) upcoming.push({ pick: k, team: teamAtPick(k) });
  return (
    <div className="space-y-3">
      <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div className="mb-1 text-xs font-black uppercase" style={{ color: THEME.signal, letterSpacing: "0.12em", fontFamily: MONO }}>Coming up</div>
        <p className="mb-2 text-xs" style={{ color: THEME.muted }}>The next twelve picks and what each of those teams still needs — the inputs that reshape the survival numbers.</p>
        <div className="flex gap-1 overflow-x-auto pb-1">
          {upcoming.map(({ pick, team }) => {
            const n = needsOf(rosters[team - 1] || []);
            const hot = Object.entries(n).filter(([k, v]) => v >= 0.9 && k !== "K" && k !== "DST").sort((a, b) => b[1] - a[1]).slice(0, 2);
            return (
              <div key={pick} className="shrink-0 rounded p-1.5 text-center" style={{ minWidth: 62, background: team === slot ? THEME.signal + "1f" : THEME.panel2, border: `1px solid ${team === slot ? THEME.signal + "55" : THEME.line}` }}>
                <div className="text-xs font-bold" style={{ fontFamily: MONO, color: team === slot ? THEME.signal : THEME.muted }}>{team === slot ? "YOU" : `T${team}`}</div>
                <div className="mt-0.5 flex justify-center gap-0.5">
                  {hot.length === 0 ? <span className="text-xs" style={{ color: THEME.dim }}>bpa</span>
                    : hot.map(([k]) => <span key={k} className="text-xs font-bold" style={{ color: POS_COLOR[k], fontFamily: MONO }}>{k}</span>)}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))" }}>
        {rosters.map((r, i) => {
          const t = i + 1, n = needsOf(r), mine = t === slot;
          return (
            <div key={t} className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${mine ? THEME.signal + "55" : THEME.line}` }}>
              <div className="mb-2 flex items-center gap-2">
                <input value={teamNames[t] ?? (mine ? "You" : `Team ${t}`)} onChange={(e) => setTeamNames({ ...teamNames, [t]: e.target.value })}
                       className="min-w-0 flex-1 rounded bg-transparent px-1 py-0.5 text-sm font-bold outline-none" style={{ color: mine ? THEME.signal : THEME.chalk, border: "1px solid transparent" }} />
                <span className="text-xs" style={{ fontFamily: MONO, color: THEME.dim }}>{r.length}/{rounds}</span>
              </div>
              <div className="mb-2 flex flex-wrap gap-1">
                {["QB", "RB", "WR", "TE"].map((pos) => {
                  const short = n[pos] >= 0.9;
                  return (
                    <span key={pos} className="rounded px-1.5 py-0.5 text-xs font-bold" style={{ fontFamily: MONO, background: short ? POS_COLOR[pos] + "22" : "transparent", color: short ? POS_COLOR[pos] : THEME.dim, border: `1px solid ${short ? POS_COLOR[pos] + "44" : THEME.line}` }}>
                      {pos} {r.filter((p) => p.pos === pos).length}
                    </span>
                  );
                })}
              </div>
              <div className="space-y-0.5">
                {r.length === 0 && <span className="text-xs" style={{ color: THEME.dim }}>No picks yet.</span>}
                {r.map((p) => (
                  <div key={p.id} className="flex items-center gap-1.5 text-xs">
                    <span className="w-7 font-bold" style={{ color: POS_COLOR[p.pos], fontFamily: MONO }}>{p.pos}</span>
                    <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ==========================================================================
   PLATFORM EDGES
   ========================================================================== */
function Arbitrage({ players, scoring, teams }) {
  const rows = useMemo(() => players.map((p) => {
    const ffc = scoring === "half" ? p.ffcHalf ?? p.ffcPPR : p.ffcPPR;
    const vals = { FFC: ffc, Sleeper: p.sleeper, ESPN: p.espn, FPros: p.fpros };
    const nums = Object.values(vals).filter((v) => v != null && v < 400);
    if (nums.length < 3) return null;
    const lo = Math.min(...nums), hi = Math.max(...nums);
    return { ...p, vals, spread: hi - lo, cheapest: Object.entries(vals).find(([, v]) => v === hi)[0], priciest: Object.entries(vals).find(([, v]) => v === lo)[0], lo, hi };
  }).filter(Boolean).sort((a, b) => b.spread - a.spread).slice(0, 30), [players, scoring]);

  return (
    <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
      <p className="mb-3 text-xs" style={{ color: THEME.muted }}>Same player, different price by platform. Draft him where he's cheapest and you get him rounds later. Sorted by widest gap.</p>
      <div className="wr-scroll">
        <div>
          <div className="grid gap-2 px-1 pb-1 text-xs font-bold uppercase" style={{ gridTemplateColumns: "2fr 60px 60px 60px 60px 1.3fr", color: THEME.dim, fontFamily: MONO, letterSpacing: "0.06em" }}>
            <div>Player</div><div>FFC</div><div>Sleeper</div><div>ESPN</div><div>FPros</div><div>Read</div>
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: "64vh" }}>
            {rows.map((p) => (
              <div key={p.id} className="grid items-center gap-2 px-1 py-2" style={{ gridTemplateColumns: "2fr 60px 60px 60px 60px 1.3fr", borderTop: `1px solid ${THEME.line}55` }}>
                <div className="flex min-w-0 items-center gap-2"><PosTag pos={p.pos} rank={p.posRank} /><span className="truncate text-sm font-semibold">{p.name}</span></div>
                {["FFC", "Sleeper", "ESPN", "FPros"].map((k) => {
                  const v = p.vals[k];
                  return <div key={k} className="text-sm" style={{ fontFamily: MONO, color: v == null ? THEME.dim : v === p.lo ? THEME.hot : v === p.hi ? THEME.cool : THEME.muted, fontWeight: v === p.lo || v === p.hi ? 700 : 400 }}>{v == null ? "—" : v.toFixed(0)}</div>;
                })}
                <div className="text-xs" style={{ color: THEME.muted }}><span style={{ color: THEME.cool }}>{Math.round(p.spread / teams)} rounds</span> cheaper on {p.cheapest} than {p.priciest}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ==========================================================================
   MY TEAM  (+ stacks/handcuffs)
   ========================================================================== */
function MyTeam({ team, roster, setRoster, byeLoad, available, pick }) {
  const slots = []; const pool = [...team];
  const fill = (pos, n) => { for (let i = 0; i < n; i++) { const idx = pool.findIndex((p) => p.pos === pos); slots.push({ label: pos, player: idx >= 0 ? pool.splice(idx, 1)[0] : null }); } };
  ["QB", "RB", "WR", "TE"].forEach((p) => fill(p, roster[p] || 0));
  for (let i = 0; i < (roster.FLEX || 0); i++) { const idx = pool.findIndex((p) => FLEX_OK.includes(p.pos)); slots.push({ label: "FLEX", player: idx >= 0 ? pool.splice(idx, 1)[0] : null }); }
  ["K", "DST"].forEach((p) => fill(p, roster[p] || 0));
  const conflicts = Object.entries(byeLoad).filter(([, n]) => n >= 3);
  const projTot = team.reduce((a, p) => a + (p.real ? p.projPts : 0), 0);

  return (
    <div className="space-y-3">
      <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div className="mb-2 flex items-baseline justify-between">
          <div className="text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Starting lineup</div>
          {projTot > 0 && <div className="text-xs" style={{ fontFamily: MONO, color: THEME.good }}>{Math.round(projTot)} proj pts</div>}
        </div>
        {slots.map((s, i) => (
          <div key={i} className="flex items-center gap-3 py-1.5" style={{ borderBottom: `1px solid ${THEME.line}55` }}>
            <span className="w-12 text-xs font-bold" style={{ color: THEME.dim, fontFamily: MONO }}>{s.label}</span>
            {s.player ? (<>
              <PosTag pos={s.player.pos} rank={s.player.posRank} />
              <span className="text-sm font-semibold">{s.player.name}</span>
              <span className="ml-auto text-xs" style={{ color: THEME.dim, fontFamily: MONO }}>{s.player.team}·b{s.player.bye}</span>
            </>) : <span className="text-sm" style={{ color: THEME.dim }}>open</span>}
          </div>
        ))}
        {pool.length > 0 && <>
          <div className="mt-3 text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Bench</div>
          {pool.map((p) => (
            <div key={p.id} className="flex items-center gap-3 py-1"><PosTag pos={p.pos} rank={p.posRank} /><span className="text-sm">{p.name}</span><span className="ml-auto text-xs" style={{ color: THEME.dim, fontFamily: MONO }}>b{p.bye}</span></div>
          ))}
        </>}
      </div>

      <StackPanel team={team} available={available} pick={pick} />

      <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Bye weeks</div>
        <div className="flex flex-wrap gap-1.5" style={{ fontFamily: MONO }}>
          {[5, 6, 7, 8, 9, 10, 11, 13, 14].map((w) => (
            <div key={w} className="rounded px-2 py-1 text-xs" style={{ background: (byeLoad[w] || 0) >= 3 ? THEME.hot + "22" : THEME.panel2, color: (byeLoad[w] || 0) >= 3 ? THEME.hot : THEME.muted, border: `1px solid ${THEME.line}` }}>W{w}·{byeLoad[w] || 0}</div>
          ))}
        </div>
        {conflicts.length > 0 && <p className="mt-2 text-xs" style={{ color: THEME.hot }}>Three or more starters are off in week{conflicts.length > 1 ? "s" : ""} {conflicts.map(([w]) => w).join(", ")}. Worth steering around from here.</p>}
      </div>

      <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Roster settings</div>
        <div className="flex flex-wrap gap-2 text-xs" style={{ fontFamily: MONO }}>
          {["QB", "RB", "WR", "TE", "FLEX", "K", "DST"].map((k) => (
            <label key={k} className="flex items-center gap-1" style={{ color: THEME.muted }}>{k}
              <input type="number" min="0" max="4" value={roster[k]} onChange={(e) => setRoster({ ...roster, [k]: Math.max(0, +e.target.value) })} className="w-12 rounded px-1 py-0.5 outline-none"
                     style={{ background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk }} /></label>
          ))}
        </div>
      </div>
    </div>
  );
}

function StackPanel({ team, available, pick }) {
  const rows = useMemo(() => {
    if (!available) return [];
    const out = [], seen = new Set();
    team.forEach((mine) => {
      available.filter((p) => p.team === mine.team && p.adp < 200).forEach((cand) => {
        let kind = null;
        if (mine.pos === "QB" && (cand.pos === "WR" || cand.pos === "TE")) kind = "stack";
        else if ((mine.pos === "WR" || mine.pos === "TE") && cand.pos === "QB") kind = "stack";
        else if (mine.pos === "RB" && cand.pos === "RB") kind = "handcuff";
        if (!kind) return;
        const key = cand.id + kind; if (seen.has(key)) return; seen.add(key);
        out.push({ ...cand, kind, because: mine.name });
      });
    });
    return out.sort((a, b) => a.adp - b.adp).slice(0, 8);
  }, [team, available]);
  if (!team.length) return null;
  return (
    <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
      <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Stacks and handcuffs</div>
      {rows.length === 0 ? <p className="text-xs" style={{ color: THEME.dim }}>Nothing on the board pairs with what you own yet.</p> :
        rows.map((p) => (
          <div key={p.id + p.kind} className="flex items-center gap-2 py-1" style={{ borderBottom: `1px solid ${THEME.line}55` }}>
            <span className="rounded px-1.5 py-0.5 text-xs font-bold" style={{ fontFamily: MONO, background: p.kind === "stack" ? THEME.cool + "22" : THEME.signal + "22", color: p.kind === "stack" ? THEME.cool : THEME.signal }}>{p.kind === "stack" ? "STACK" : "CUFF"}</span>
            <PosTag pos={p.pos} rank={p.posRank} />
            <span className="min-w-0 flex-1 truncate text-sm">{p.name}</span>
            <span className="truncate text-xs" style={{ color: THEME.dim, maxWidth: 110 }}>with {p.because.split(" ").slice(-1)[0]}</span>
            <span className="text-xs" style={{ fontFamily: MONO, color: THEME.muted }}>{p.adp.toFixed(0)}</span>
            <button onClick={() => pick(p.id, "me")} className="rounded px-1.5 py-0.5 text-xs font-bold" style={{ background: THEME.signal, color: "#141007" }}>+</button>
          </div>
        ))}
    </div>
  );
}

/* ==========================================================================
   SYNC
   ========================================================================== */
function SyncPanel({ sleeperUser, setSleeperUser, findLeagues, leagues, draftId, setDraftId, pullPicks, autoSync, setAutoSync, syncState, importPasted, setTeams, setRounds, setBudget, loadProjections, importProjCsv, hasProj }) {
  const [paste, setPaste] = useState("");
  const fileRef = useRef(null);
  const onFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    const rdr = new FileReader();
    rdr.onload = () => importProjCsv(String(rdr.result));
    rdr.readAsText(f);
    e.target.value = "";
  };
  const tone = syncState.status === "error" ? THEME.hot : syncState.status === "ok" ? THEME.cool : THEME.muted;
  return (
    <div className="space-y-3">
      <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div className="mb-2 flex items-center justify-between">
          <div className="text-xs font-black uppercase" style={{ color: THEME.signal, letterSpacing: "0.12em", fontFamily: MONO }}>Projections</div>
          <button onClick={loadProjections} className="rounded px-3 py-1 text-xs font-bold" style={{ background: hasProj ? THEME.panel2 : THEME.good, color: hasProj ? THEME.good : "#0A100D", border: `1px solid ${THEME.line}` }}>{hasProj ? "Refresh" : "Load from Sleeper"}</button>
        </div>
        <p className="text-xs" style={{ color: THEME.good }}>Built-in RotoBaller projections are loaded by default. Re-upload a fresh CSV close to draft day to refresh them.</p>
        <p className="mt-1 text-xs" style={{ color: THEME.muted }}>Two ways in. A projection CSV (RotoBaller, Sharp, FantasyPros — any export with player + points columns) is the reliable, richer source. The Sleeper button is the one-click fallback. Either fills the same Proj / Edge / Opp columns.</p>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs" style={{ fontFamily: MONO }}>
          <input ref={fileRef} type="file" accept=".csv,text/csv" onChange={onFile} style={{ display: "none" }} />
          <button onClick={() => fileRef.current?.click()} className="rounded px-3 py-1 font-bold" style={{ background: THEME.good, color: "#0A100D" }}>Upload projection CSV</button>
          <span style={{ color: THEME.dim }}>columns auto-detected · matches by name</span>
        </div>
      </div>

      <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.signal, letterSpacing: "0.12em", fontFamily: MONO }}>Sleeper live draft</div>
        <p className="mb-3 text-xs" style={{ color: THEME.muted }}>Enter your username, choose the league, then turn on auto-sync before the draft starts and the board marks picks as they happen.</p>
        <div className="flex flex-wrap items-center gap-2 text-xs" style={{ fontFamily: MONO }}>
          <input value={sleeperUser} onChange={(e) => setSleeperUser(e.target.value)} placeholder="sleeper username" className="w-48 rounded px-2 py-1 outline-none" style={{ background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk }} />
          <button onClick={findLeagues} className="rounded px-3 py-1 font-bold" style={{ background: THEME.signal, color: "#141007" }}>Find leagues</button>
          {draftId && <>
            <button onClick={pullPicks} className="rounded px-3 py-1" style={{ background: THEME.panel2, color: THEME.chalk, border: `1px solid ${THEME.line}` }}>Sync now</button>
            <label className="flex items-center gap-1" style={{ color: THEME.muted }}><input type="checkbox" checked={autoSync} onChange={(e) => setAutoSync(e.target.checked)} /> auto every 8s</label>
          </>}
        </div>
        {syncState.msg && <p className="mt-2 text-xs" style={{ color: tone }}>{syncState.msg}</p>}
        {leagues.length > 0 && (
          <div className="mt-3 space-y-1">
            {leagues.map((lg) => (
              <button key={lg.draft.draft_id} onClick={() => { setDraftId(lg.draft.draft_id); if (lg.teams) setTeams(lg.teams); const rr = lg.draft.settings?.rounds; if (rr) setRounds(rr); const bb = lg.draft.settings?.budget; if (bb) setBudget(bb); }}
                      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs"
                      style={{ background: draftId === lg.draft.draft_id ? THEME.signal + "1f" : THEME.panel2, border: `1px solid ${draftId === lg.draft.draft_id ? THEME.signal + "55" : THEME.line}`, color: THEME.chalk }}>
                <span className="flex-1 font-semibold">{lg.name}</span>
                <span style={{ fontFamily: MONO, color: THEME.muted }}>{lg.teams}tm · {lg.draft.type} · {lg.draft.status}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-lg p-3" style={{ background: THEME.panel, border: `1px solid ${THEME.line}` }}>
        <div className="mb-2 text-xs font-black uppercase" style={{ color: THEME.muted, letterSpacing: "0.12em", fontFamily: MONO }}>Paste import</div>
        <p className="mb-2 text-xs" style={{ color: THEME.muted }}>For ESPN and Yahoo, which need a login. Copy the drafted-players list from the draft room — one name per line or comma separated. Your own "mine" marks stay.</p>
        <textarea value={paste} onChange={(e) => setPaste(e.target.value)} rows={5} placeholder={"Jahmyr Gibbs\nJa'Marr Chase\nPuka Nacua"} className="w-full rounded px-2 py-1 text-xs outline-none" style={{ background: THEME.panel2, border: `1px solid ${THEME.line}`, color: THEME.chalk, fontFamily: MONO }} />
        <button onClick={() => importPasted(paste)} className="mt-2 rounded px-3 py-1 text-xs font-bold" style={{ background: THEME.signal, color: "#141007" }}>Import names</button>
      </div>
    </div>
  );
}
