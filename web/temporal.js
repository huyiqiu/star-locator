// Browser-camera equivalent of star16/temporal.py. Uploads and simulations stay raw.
const fields=['camera_position_marker_mm'];
const median=values=>{const ordered=[...values].sort((a,b)=>a-b);const n=ordered.length;return n%2?ordered[(n-1)/2]:(ordered[n/2-1]+ordered[n/2])/2};

export class TemporalPoseFilter{
 constructor(){this.tracks=new Map()}
 reset(){this.tracks.clear()}
 apply(packet,nowMs){
  const smoothingMs=Number(packet.config?.temporal_smoothing_ms??300);
  if(smoothingMs<=0)this.reset();
  const counts=new Map();for(const marker of packet.markers||[])counts.set(marker.id,(counts.get(marker.id)||0)+1);
  for(const marker of packet.markers||[]){
   const pose=marker.pose;
   if(!pose||!['valid','approximate'].includes(marker.pose_status)||counts.get(marker.id)!==1)continue;
   const filtered={};
   for(const field of fields){
    const raw=pose[field];if(!Array.isArray(raw)||raw.length!==3||!raw.every(Number.isFinite))continue;
    const key=`${marker.id}:${field}`;
    if(smoothingMs<=0){filtered[field]=[...raw];continue}
    let track=this.tracks.get(key);
    if(!track||nowMs-track.last>350||nowMs<=track.last){track={samples:[],value:null,last:nowMs};this.tracks.set(key,track)}
    const dt=track.samples.length?nowMs-track.last:0;
    track.samples.push([...raw]);if(track.samples.length>3)track.samples.shift();track.last=nowMs;
    if(track.value===null)track.value=[...raw];
    else if(track.samples.length===3){
     const center=[0,1,2].map(i=>median(track.samples.map(v=>v[i])));
     const innovation=Math.hypot(...center.map((v,i)=>v-track.value[i]));
     const tau=Math.max(40,smoothingMs/(1+innovation/50));
     const alpha=-Math.expm1(-dt/tau);
     track.value=track.value.map((v,i)=>v+alpha*(center[i]-v));
    }
    filtered[field]=[...track.value];
   }
   if(filtered.camera_position_marker_mm)filtered.distance_mm=Math.hypot(...filtered.camera_position_marker_mm);
   for(const field of [...fields,'distance_mm'])if(filtered[field]!=null)filtered[field.replace(/_mm$/,'_cm')]=Array.isArray(filtered[field])?filtered[field].map(v=>v/10):filtered[field]/10;
   pose.filtered=filtered;pose.filter_status=filtered.camera_position_marker_mm?'ready':'unavailable';
  }
  // Missing frames display no pose; short gaps retain history for reacquisition.
  for(const [key,track] of this.tracks)if(nowMs-track.last>350)this.tracks.delete(key);
  return packet;
 }
}
