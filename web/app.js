import {createCalibrationUI} from '/assets/calibration.js?v=2.0.0';
import {TemporalPoseFilter} from '/assets/temporal.js?v=2.0.0';
const $=id=>document.getElementById(id);
const state={source:'uvc',mode:'glow',family:null,config:null,intrinsics:null,result:null,selected:0,selectedId:null,stream:null,ws:null,running:false,session:0,lastLive:0,lastRendered:0,lastFile:null,lastImage:null,receivedAt:0,cameraOwned:false,defaultConfig:null};
const ctx=$('viewer').getContext('2d');
let toastTimer,paintSequence=0,calibrationUI;
const browserPoseFilter=new TemporalPoseFilter();
function toast(message,error=false){$('toast').textContent=message;$('toast').className='toast visible'+(error?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),error?7000:3200)}
async function api(path,options={}){const response=await fetch(path,options);let result;try{result=await response.json()}catch{throw Error('服务没有返回有效 JSON，请确认本机算法服务已运行。')}if(!response.ok){const detail=result.detail;throw Error(typeof detail==='string'?detail:Array.isArray(detail)?detail.map(d=>`${d.loc?.slice(1).join('.')||'参数'}: ${d.msg}`).join('\n'):JSON.stringify(detail)||`HTTP ${response.status}`)}return result}
async function detectBlob(blob,filename='frame.jpg'){const form=new FormData();form.append('image',blob,filename);form.append('include_images','true');if(state.config)form.append('config',JSON.stringify(state.config));if(state.intrinsics)form.append('intrinsics',JSON.stringify(state.intrinsics));return api('/api/v1/detect',{method:'POST',body:form})}
const jsonOptions=data=>({headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const toCm=millimeters=>millimeters/10;
const toMm=centimeters=>centimeters*10;
function on(id,fn,event='click'){$(id).addEventListener(event,async e=>{try{await fn(e)}catch(error){console.error(error);toast(error.message||String(error),true)}})}
function busy(button,value){button.disabled=value;button.classList.toggle('busy',value)}
function download(name,text,type='application/json'){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function route(){const name=['live','family','calibration','api'].includes(location.hash.slice(1))?location.hash.slice(1):'live';document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id===`page-${name}`));document.querySelectorAll('[data-page]').forEach(el=>el.classList.toggle('active',el.dataset.page===name));$('crumb').textContent={live:'实时取景',family:'标记基准族',calibration:'相机标定',api:'开发者接口'}[name];if(calibrationUI)calibrationUI.setActive(name==='calibration').catch(e=>toast(e.message,true));if(name==='live')paint()}
window.addEventListener('hashchange',route);route();
function drawGrid(element,grid){element.replaceChildren();for(let r=0;r<4;r++)for(let c=0;c<4;c++){const dot=document.createElement('i');dot.className=grid?.[r]?.[c]?'on'+((r===0||r===3)&&(c===0||c===3)?' anchor':''):'';element.appendChild(dot)}}
function selectedMarker(){return state.result?.markers?.[state.selected]||null}
const poseLabels={valid:'有效',approximate:'粗略估计',ambiguous:'平面双解',calibration_required:'待相机标定',reprojection_rejected:'重投影拒绝'};
function showResult(result){
 if(state.source==='browser')browserPoseFilter.apply(result,performance.now());
 state.result=result;state.receivedAt=performance.now();
 const selectedIndex=result.markers.findIndex(m=>m.id===state.selectedId);
 state.selected=selectedIndex>=0?selectedIndex:0;
 if(result.markers.length)state.selectedId=result.markers[state.selected].id;
 if(['uvc','browser'].includes(state.source))state.lastLive=performance.now();
 $('viewer-empty').hidden=true;$('frame-size').textContent=`${result.width} × ${result.height}`;
 $('frame-clock').textContent=new Date().toLocaleTimeString('zh-CN',{hour12:false});
 $('tracking-status').textContent=result.markers?.length?`${result.markers.length} 个标记`:`${result.diagnostics?.blob_count||0} 个亮点 · 未解码`;
 $('latency').textContent=`${result.timing_ms.toFixed(1)} ms`;$('latency').title=result.timings?`算法 ${result.timings.detection_ms.toFixed(1)} ms / 图像编码 ${result.timings.image_encoding_ms.toFixed(1)} ms / 处理合计 ${result.timings.total_ms.toFixed(1)} ms`:'';
 if(state.source==='uvc'&&result.rates){const r=result.rates;$('source-state').textContent=`采集 ${r.capture_fps?.toFixed(1)||'—'} / 处理 ${r.processing_fps?.toFixed(1)||'—'} fps`;$('frame-rate').title=$('source-state').textContent}
 const stats=result.image_statistics;$('image-stats').textContent=stats?`灰度 ${stats.min}–${stats.max} · 均值 ${stats.mean.toFixed(1)} · P99 ${stats.p99.toFixed(0)}`:'';
 const d=result.diagnostics;$('pipeline-stats').textContent=`${d.blob_count} BLOBS / ${d.candidate_quads} QUADS / ${d.matched_dots} MATCHED DOTS`;
 const selector=$('marker-selector');selector.replaceChildren();if(result.markers.length>1)result.markers.forEach((m,i)=>{const b=document.createElement('button');b.textContent=`ID ${String(m.id).padStart(3,'0')}`;b.classList.toggle('selected',i===state.selected);b.onclick=()=>{state.selected=i;state.selectedId=m.id;updateObservation();paint()};selector.appendChild(b)});
 updateObservation();paint();
 const compact={...result};delete compact.images;delete compact.config;delete compact.ground_truth;
 $('live-json').textContent=JSON.stringify(compact,null,2);
}
function updateObservation(){
 const m=selectedMarker(),pose=m?.pose;
 const live=['uvc','browser'].includes(state.source);
 const position=live?pose?.filtered:pose;
 $('detected-id').replaceChildren(document.createTextNode(m?String(m.id).padStart(3,'0'):'—'));const label=document.createElement('span');label.textContent='STAR16-V2';$('detected-id').appendChild(label);
 drawGrid($('mini-grid'),m?state.family.markers.find(x=>x.id===m.id)?.grid:null);
 $('quality-value').textContent=m?`${(m.quality_score*100).toFixed(1)} / 100`:'—';$('quality-bar').style.width=m?`${m.quality_score*100}%`:'0%';
 $('matched-count').textContent=m?`${m.dot_indices.length} 点`:'—';$('hamming-value').textContent=m?m.corrected?`已修正 ${m.hamming_distance} 位`:'精确匹配':'—';
 $('pose-status').textContent=m?poseLabels[m.pose_status]:'无有效标记';$('observation-chip').textContent=m?'已识别':state.result?'亮点调试':'等待输入';
 $('relative-distance').textContent=position?.distance_cm!=null?`${position.distance_cm.toFixed(1)} cm`:'—';
 $('relative-angle').textContent=m?`${m.direction_deg.toFixed(1)}°`:'—';
 const p=position?.camera_position_marker_cm;['x','y','z'].forEach((axis,i)=>{const value=p?p[i].toFixed(1):'—';$(`pose-${axis}`).textContent=value;$(`hud-${axis}`).textContent=value});
 $('fullscreen-hud').classList.toggle('empty',!m);
 $('hud-status').textContent=m?(poseLabels[m.pose_status]||m.pose_status):'无有效标记';
 $('hud-marker').textContent=m?`ID ${String(m.id).padStart(3,'0')}`:'ID —';
 $('hud-distance').textContent=position?.distance_cm!=null?`${position.distance_cm.toFixed(1)} cm`:'距离 —';
 $('hud-angle').textContent=m?`${m.direction_deg.toFixed(1)}°`:'方向 —';
 document.querySelectorAll('#marker-selector button').forEach((b,i)=>b.classList.toggle('selected',i===state.selected));
 let note='',warn=false;
 if(state.source==='simulation')note='当前是合成演示，使用真实检测算法和已知合成内参。识别与位姿用于验证算法链路，不代表现场精度。';
 else if(!m){note=`检测到 ${state.result?.diagnostics?.blob_count||0} 个候选亮点。随机贴点可用于调试阈值与中心提取；需按基准族布局制作标记，才能解码 ID、方向与位姿。`;}
 else if(m.pose_status==='calibration_required'){note='ID 与方向已识别。请在“相机标定”中导入真实内参或完成棋盘格标定，厘米位姿随后生效。';warn=true}
 else if(m.pose_status==='ambiguous'){note='当前平面位姿存在两个近似等价解。可增加观察倾角或引入安装姿态、里程计约束，当前结果已标记为歧义。';warn=true}
 else if(m.pose_status==='reprojection_rejected'){note='图案已识别，但位姿没有通过几何质量门限。请检查标定、实际点间距、反光点粘贴位置及曝光。';warn=true}
 else if(m.pose_status==='approximate'){note='点阵已精确解码，当前位姿为粗略估计。建议检查标定与反光点位置，并仅将其作为参考。';warn=true}
 else note=`已输出相机光心相对标记 ID ${String(m.id).padStart(3,'0')} 的位姿。世界坐标与机器人坐标请由集成应用自行变换。`;
 if(state.result?.warnings?.includes('calibration_aspect_mismatch')){note='当前画面宽高比与标定分辨率不同，已停止位姿输出。请恢复标定时的取景格式或重新标定。';warn=true}
 if(!m&&state.result?.warnings?.includes('undistortion_unstable')){note='画面中的部分点无法用当前畸变参数稳定还原，边缘位置可能无法识别。请先将标记移近画面中心验证，并用覆盖画面边缘的棋盘视图重新标定。';warn=true}
 $('notice-text').textContent=note;$('live-notice').classList.toggle('warn',warn);
 $('frame-note').textContent=state.source==='simulation'?'合成演示 · 独立 3D 投影器 → 真实检测链路':m?`已解码 ${state.result.markers.length} 个标记 · ${m.pose_status==='calibration_required'?'等待标定':'PnP '+(poseLabels[m.pose_status]||'未知状态')}`:`亮点调试 · ${state.result?.diagnostics?.blob_count||0} 个候选点 · 尚无有效编码`;
}
async function paint(){
 if(!state.result?.images||!$('page-live').classList.contains('active'))return;
 const seq=++paintSequence;
 let key=state.mode;if(key==='glow'&&$('show-unencoded').checked)key='blobs';
 const src=state.result.images[key];if(!src)return;
 const img=new Image();img.src=src;await img.decode();if(seq!==paintSequence)return;
 state.lastImage=img;const canvas=$('viewer');canvas.width=state.result.width;canvas.height=state.result.height;ctx.drawImage(img,0,0);
 if(!$('show-overlay').checked)return;
 const scale=canvas.width/960;ctx.lineWidth=1.2*scale;ctx.font=`${11*scale}px ui-monospace,monospace`;
 const assigned=new Set(state.result.markers.flatMap(m=>m.dot_indices));
 if($('show-unencoded').checked&&state.mode!=='binary')state.result.blobs.forEach((b,i)=>{if(assigned.has(i))return;ctx.strokeStyle='#d7ba79aa';ctx.beginPath();ctx.arc(b.x,b.y,b.radius+4*scale,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#c1aa77';ctx.fillText(`${i+1}`,b.x+b.radius+6*scale,b.y-5*scale)});
 state.result.markers.forEach((m,index)=>{
  const p=m.corners_px;ctx.strokeStyle=index===state.selected?'#b6f578':'#7ab599';ctx.lineWidth=(index===state.selected?1.8:1)*scale;ctx.setLineDash([6*scale,4*scale]);ctx.beginPath();p.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.closePath();ctx.stroke();ctx.setLineDash([]);
  const top=Math.min(...p.map(x=>x[1]))-20*scale,left=Math.min(...p.map(x=>x[0]));
  const label=`ID ${String(m.id).padStart(3,'0')}  /  ${m.direction_deg.toFixed(1)}°`;
  ctx.fillStyle='#162a1de6';ctx.fillRect(left-1*scale,top-13*scale,ctx.measureText(label).width+18*scale,21*scale);ctx.fillStyle='#b6f578';ctx.fillText(label,left+7*scale,top+1*scale);
  m.dot_indices.forEach(i=>{const b=state.result.blobs[i];ctx.strokeStyle='#b6f57866';ctx.beginPath();ctx.arc(b.x,b.y,b.radius+3*scale,0,Math.PI*2);ctx.stroke()});
  const center=m.center_px;const x=[(p[1][0]-p[0][0])*.25,(p[1][1]-p[0][1])*.25];const y=[(p[3][0]-p[0][0])*.25,(p[3][1]-p[0][1])*.25];
  [[x,'#e8a18b','X'],[y,'#a7df88','Y']].forEach(([v,color,text])=>{ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=1.5*scale;ctx.beginPath();ctx.moveTo(...center);ctx.lineTo(center[0]+v[0],center[1]+v[1]);ctx.stroke();ctx.fillText(text,center[0]+v[0]+5*scale,center[1]+v[1]+2*scale)});
 });
 ctx.strokeStyle='#7f9a8570';ctx.lineWidth=1*scale;const cx=canvas.width/2,cy=canvas.height/2;ctx.beginPath();ctx.moveTo(cx-8*scale,cy);ctx.lineTo(cx+8*scale,cy);ctx.moveTo(cx,cy-8*scale);ctx.lineTo(cx,cy+8*scale);ctx.stroke();
}
const emptyHints={simulation:'正在生成合成图像，用于验证算法链路。',browser:'请允许浏览器相机权限后重试。',uvc:'正在连接 USB 相机，也可改用合成演示验证算法链路。',image:'选择一张红外图像进行检测。'};
function clearObservation(message='等待图像输入',detail=emptyHints[state.source]||'确认设备连接后重试。'){
 browserPoseFilter.reset();
 state.result=null;state.selected=0;state.selectedId=null;paintSequence++;ctx.clearRect(0,0,$('viewer').width,$('viewer').height);$('viewer-empty').hidden=false;$('viewer-empty').querySelector('strong').textContent=message;$('viewer-empty').querySelector('p').textContent=detail;$('tracking-status').textContent=message;$('latency').textContent='— ms';$('live-json').textContent='无新鲜有效帧';$('marker-selector').replaceChildren();$('pipeline-stats').textContent='等待算法返回诊断信息';$('image-stats').textContent='等待图像统计';updateObservation();
}
function showLiveNotice(message,warn=false){$('notice-text').textContent=message;$('live-notice').classList.toggle('warn',warn);$('live-notice').hidden=false}
function hideLiveNotice(){$('live-notice').hidden=true;$('live-notice').classList.remove('warn')}
function simulationOptions(){return {marker_id:Number($('sim-id').value),yaw_deg:Number($('sim-yaw').value),distance_mm:toMm(Number($('sim-distance').value)),noise_sigma:Number($('sim-noise').value),tilt_x_deg:Number($('sim-tiltx').value),tilt_y_deg:Number($('sim-tilty').value),missing_cell:$('sim-missing').value===''?null:Number($('sim-missing').value),second_marker:$('sim-second').checked}}
async function renderSimulation(){const token=state.session;busy($('render-sim'),true);try{const result=await api('/api/simulate',{method:'POST',...jsonOptions(simulationOptions())});if(token===state.session&&state.source==='simulation')showResult(result)}finally{busy($('render-sim'),false)}}
async function stopCapture(){
 state.session++;state.running=false;state.lastLive=0;if(state.ws){state.ws.onclose=null;state.ws.close();state.ws=null}if(state.stream){state.stream.getTracks().forEach(t=>t.stop());state.stream=null;$('camera-video').srcObject=null}
 if(state.cameraOwned){state.cameraOwned=false;await api('/api/camera/stop',{method:'POST'})}
}
async function setSource(source){
 await stopCapture();state.source=source;clearObservation();
 document.querySelectorAll('#source-tabs button').forEach(b=>b.classList.toggle('selected',b.dataset.source===source));['simulation','browser','uvc','image'].forEach(s=>$(`source-${s}`).hidden=s!==source);
 const names={simulation:'合成演示',browser:'浏览器相机',uvc:'后端 UVC',image:'上传图像'};$('source-chip').textContent=names[source];$('capture-badge').className='capture-badge'+(source==='simulation'?'':' real');$('capture-badge').replaceChildren();const dot=document.createElement('i');$('capture-badge').append(dot,document.createTextNode({simulation:'SYNTHETIC / 合成演示',browser:'USB CAMERA / 浏览器采集',uvc:'USB CAMERA / 后端采集',image:'UPLOADED / 单帧图像'}[source]));$('source-state').textContent=source==='simulation'?'可复现测试':'等待连接';$('frame-rate').textContent=['simulation','image'].includes(source)?'单帧模式':'未连接';
 if(source==='simulation')await renderSimulation();
 if(source==='browser')await refreshDevices();
 if(source==='uvc')await refreshUvcControls();
}
async function refreshDevices(){if(!navigator.mediaDevices){throw Error('当前浏览器不支持相机访问，请使用桌面浏览器打开 localhost，或使用后端 UVC 接入。')}const devices=await navigator.mediaDevices.enumerateDevices();const selected=$('browser-device').value;$('browser-device').replaceChildren();devices.filter(d=>d.kind==='videoinput').forEach((d,i)=>{const o=document.createElement('option');o.value=d.deviceId;o.textContent=d.label||`相机 ${i+1}（授权后显示名称）`;$('browser-device').appendChild(o)});if(selected)$('browser-device').value=selected;if(!$('browser-device').options.length){const o=document.createElement('option');o.value='';o.textContent='默认相机（点击连接以授权）';$('browser-device').append(o)}}
async function startBrowser(){
 await stopCapture();clearObservation('正在连接相机');
 const deviceId=$('browser-device').value;
 try{state.stream=await navigator.mediaDevices.getUserMedia({video:{...(deviceId?{deviceId:{exact:deviceId}}:{}),width:{ideal:1280},height:{ideal:720}},audio:false})}catch(e){clearObservation('无法访问相机');throw Error(`相机访问失败：${e.name}。请允许浏览器相机权限，并关闭其他占用该相机的程序。`)}
 const video=$('camera-video');video.srcObject=state.stream;await video.play();state.running=true;const token=state.session;state.lastLive=performance.now();await refreshDevices();$('source-state').textContent='真实相机已连接';
 state.stream.getVideoTracks().forEach(track=>track.addEventListener('ended',()=>{state.running=false;clearObservation('相机设备已断开');$('source-state').textContent='设备已断开'}));
 const cap=document.createElement('canvas');let lastVideoTime=-1,videoUpdatedAt=performance.now();
 async function loop(){
  if(!state.running||token!==state.session)return;
  const t0=performance.now();
  try{if(video.readyState<2)throw Error('相机暂未输出图像');if(video.currentTime!==lastVideoTime){lastVideoTime=video.currentTime;videoUpdatedAt=performance.now()}else if(performance.now()-videoUpdatedAt>1000)throw Error('相机没有新鲜视频帧，已停止位姿输出');cap.width=video.videoWidth;cap.height=video.videoHeight;cap.getContext('2d').drawImage(video,0,0);const blob=await new Promise(resolve=>cap.toBlob(resolve,'image/jpeg',.9));if(!blob)throw Error('无法编码相机帧');const result=await detectBlob(blob,'frame.jpg');if(token!==state.session)return;result.source='browser';showResult(result);$('frame-rate').textContent=`${(1000/(performance.now()-t0)).toFixed(1)} fps`;}
  catch(error){if(token!==state.session)return;clearObservation('图像处理失败');toast(error.message,true);state.running=false;state.stream?.getTracks().forEach(t=>t.stop());return}
  setTimeout(loop,Math.max(0,100-(performance.now()-t0)));
 }
 loop();
}
function refreshStreamMode(){
 if(state.source==='uvc'&&state.running&&state.ws){state.ws.onclose=null;state.ws.onmessage=null;state.ws.onerror=null;state.ws.close();state.lastRendered=0;connectWebSocket()}
}
function connectWebSocket(){
 const token=state.session;state.running=true;state.lastLive=performance.now();const imageMode=state.mode==='glow'&&$('show-unencoded').checked?'blobs':state.mode;const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/camera?image_mode=${imageMode}`);state.ws=ws;
 ws.onmessage=e=>{if(token!==state.session)return;const result=JSON.parse(e.data);if(result.stale){clearObservation(result.status?.error||'等待新鲜相机帧');return}if(result.images){const now=performance.now();$('frame-rate').textContent=state.lastRendered?`显示 ${(1000/(now-state.lastRendered)).toFixed(1)} fps`:'采集中';state.lastRendered=now;showResult(result)}};
 ws.onerror=()=>toast('相机数据流连接失败，请检查本机服务。',true);ws.onclose=()=>{if(token===state.session){state.running=false;clearObservation('相机数据流已断开')}};
}
async function startUVC(){
 if($('start-uvc').disabled)return;
 busy($('start-uvc'),true);$('stop-uvc').disabled=true;
 try{await stopCapture();clearObservation('正在打开 UVC 相机，等待首帧');$('source-state').textContent='正在连接…';const [width,height]=$('uvc-resolution').value.split(',').map(Number);const status=await api('/api/camera/start',{method:'POST',...jsonOptions({index:Number($('uvc-index').value),width,height,fps:Number($('uvc-fps').value)})});state.cameraOwned=true;$('source-state').textContent=`真实设备 ${status.device.index} · ${status.device.width} × ${status.device.height}`;hideLiveNotice();connectWebSocket();await refreshUvcControls()}
 catch(error){$('source-state').textContent='连接失败';clearObservation('USB 相机连接失败',error.message);showLiveNotice(error.message,true);throw error}
 finally{busy($('start-uvc'),false);$('stop-uvc').disabled=false}
}
function setUvcControlAvailability(available){
 const manual=$('uvc-exposure-mode').value==='manual';
 $('uvc-exposure-mode').disabled=!available;
 $('uvc-exposure-ms').disabled=!available||!manual;
 $('uvc-gain').disabled=!available||!manual;
 $('apply-uvc-controls').disabled=!available;
 $('reset-uvc-controls').disabled=!available;
}
function showUvcControls(data,message='已读取相机实际设置'){
 $('uvc-controls-device').textContent=`${data.device.name} · ${data.device.vendor_id}:${data.device.product_id}`;
 $('uvc-exposure-mode').value=data.mode;
 const exposure=data.exposure_100us,gain=data.gain;
 $('uvc-exposure-ms').min=String(exposure.minimum/10);
 $('uvc-exposure-ms').max=String(exposure.maximum/10);
 $('uvc-exposure-ms').step=String((exposure.step_size||1)/10);
 $('uvc-exposure-ms').value=String(exposure.current_value/10);
 $('uvc-gain').min=String(gain.minimum);
 $('uvc-gain').max=String(gain.maximum);
 $('uvc-gain').step=String(gain.step_size||1);
 $('uvc-gain').value=String(gain.current_value);
 $('uvc-controls-status').textContent=`${message} · ${exposure.current_value/10} ms / 增益 ${gain.current_value}`;
 if(data.saved)$('uvc-controls-status').textContent+=` · 重启后自动应用：${data.saved.mode==='manual'?`${(data.saved.exposure_100us/10).toFixed(1)} ms / 增益 ${data.saved.gain}`:'自动曝光'}`;
 setUvcControlAvailability(true);
}
async function refreshUvcControls(){
 try{const data=await api('/api/camera/controls');showUvcControls(data);return data}
 catch(error){$('uvc-controls-device').textContent='无法读取 UVC 硬件控制项';$('uvc-controls-status').textContent=error.message;setUvcControlAvailability(false);return null}
}
async function applyUvcControls(event){
 event.preventDefault();
 const mode=$('uvc-exposure-mode').value;
 const body={mode};
 if(mode==='manual'){
  if(!$('uvc-controls-form').reportValidity())return;
  body.exposure_100us=Math.round(Number($('uvc-exposure-ms').value)*10);
  body.gain=Number($('uvc-gain').value);
 }
 busy($('apply-uvc-controls'),true);
 try{showUvcControls(await api('/api/camera/controls',{method:'PUT',...jsonOptions(body)}),'已应用并回读确认');toast('相机曝光与增益已应用，重启后自动恢复')}
 finally{busy($('apply-uvc-controls'),false)}
}
async function resetUvcControls(){
 busy($('reset-uvc-controls'),true);
 try{showUvcControls(await api('/api/camera/controls/reset',{method:'POST'}),'已恢复相机硬件默认值 · 自动应用已清除');toast('相机曝光与增益已恢复默认')}
 finally{busy($('reset-uvc-controls'),false)}
}
async function processFile(file){state.lastFile=file;const token=state.session;const result=await detectBlob(file,file.name||'frame.jpg');if(token===state.session){showResult(result);$('upload-name').textContent=file.name;$('source-state').textContent='真实图像 · 单帧'}}
function configToForm(c){$('cfg-spacing').value=toCm(c.spacing_mm);$('cfg-diameter').value=toCm(c.dot_diameter_mm);$('cfg-threshold-mode').value=c.threshold_mode;$('cfg-threshold').value=c.threshold;$('threshold-output').textContent=c.threshold;$('cfg-min-area').value=c.min_area;$('cfg-max-area').value=c.max_area;$('cfg-circularity').value=c.min_circularity;$('cfg-reprojection').value=c.max_reprojection_error_px;$('cfg-edge-pose-error').value=c.edge_pose_max_reprojection_error_px??20;$('cfg-hamming').value=c.max_hamming;$('cfg-blur').value=c.blur_size;$('cfg-smoothing').value=c.temporal_smoothing_ms??300;$('cfg-threshold').disabled=c.threshold_mode!=='fixed'}
function formToConfig(){return {...state.config,spacing_mm:toMm(Number($('cfg-spacing').value)),dot_diameter_mm:toMm(Number($('cfg-diameter').value)),threshold_mode:$('cfg-threshold-mode').value,threshold:Number($('cfg-threshold').value),min_area:Number($('cfg-min-area').value),max_area:Number($('cfg-max-area').value),min_circularity:Number($('cfg-circularity').value),max_reprojection_error_px:Number($('cfg-reprojection').value),edge_pose_max_reprojection_error_px:Number($('cfg-edge-pose-error').value),max_hamming:Number($('cfg-hamming').value),blur_size:Number($('cfg-blur').value),temporal_smoothing_ms:Number($('cfg-smoothing').value)}}
async function reprocess(){if(state.source==='simulation')await renderSimulation();else if(state.source==='image'&&state.lastFile)await processFile(state.lastFile)}
function initFamily(family){
 $('family-count').textContent=String(family.markers.length).padStart(2,'0');$('family-nav-count').textContent=String(family.markers.length).padStart(2,'0');$('family-distance').textContent=String(family.minimum_d4_hamming_distance).padStart(2,'0');
 $('family-grid').replaceChildren();family.markers.forEach(m=>{
  const card=document.createElement('div');card.className='panel family-card';
  card.innerHTML=`<div class="family-card-head"><strong>STAR / ${String(m.id).padStart(3,'0')}</strong><span>0x${m.code_hex}</span></div><div class="family-pattern"><div class="dot-grid"></div><span class="family-direction">+X → &nbsp; +Y ↓</span></div><div class="family-dot-note">${m.dot_count} 个反光点 · 四角锚点固定</div><div class="family-card-actions"><button class="text-button" data-action="demo">在取景器中测试 ↗</button><button class="button secondary" data-action="svg">↓ SVG</button><button class="text-button" data-action="cut">裁切模板</button></div>`;
  drawGrid(card.querySelector('.dot-grid'),m.grid);
  card.querySelector('[data-action="demo"]').onclick=async()=>{try{$('sim-id').value=m.id;location.hash='#live';await setSource('simulation')}catch(e){toast(e.message,true)}};
  const getSvg=async template=>{const spacing=Number($('family-spacing').value),diameter=Number($('family-diameter').value);if(!spacing||!diameter||diameter>spacing*.8){toast('反光点直径须不超过点中心间距的 80%。',true);return}const response=await fetch(`/api/family/${m.id}/svg?spacing_mm=${toMm(spacing)}&diameter_mm=${toMm(diameter)}&template=${template}`);if(!response.ok){toast('尺寸无效，请检查间距和直径。',true);return}download(`star16-v2-${String(m.id).padStart(3,'0')}${template?'-cutting':''}.svg`,await response.text(),'image/svg+xml')};
  card.querySelector('[data-action="svg"]').onclick=()=>getSvg(false).catch(e=>toast(e.message,true));card.querySelector('[data-action="cut"]').onclick=()=>getSvg(true).catch(e=>toast(e.message,true));$('family-grid').append(card);
 });
 family.markers.forEach(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=`${String(m.id).padStart(3,'0')} / 0x${m.code_hex}`;$('sim-id').append(o)});
 for(let i=0;i<16;i++){const o=document.createElement('option');o.value=i;o.textContent=`第 ${Math.floor(i/4)+1} 行，第 ${i%4+1} 列`;$('sim-missing').append(o)}
}
async function saveIntrinsics(){const data=JSON.parse($('intrinsics-json').value);data.calibrated=$('confirm-calibrated').checked;if(!data.calibrated)throw Error('请先完成真实标定，或确认导入参数来自本相机。');const intr=await api('/api/intrinsics',{method:'PUT',...jsonOptions(data)});state.intrinsics=intr;$('intrinsics-json').value=JSON.stringify(intr,null,2);$('calibration-badge').textContent='已保存真实内参';toast('相机内参已保存，后续真实帧将计算位姿。');await reprocess()}
function initApi(){
 $('curl-example').textContent=`curl -X POST ${location.origin}/api/v1/detect \\\n  -F 'image=@infrared.jpg' \\\n  -F 'intrinsics=<camera-intrinsics.json' \\\n  -F 'config=<detector.json' \\\n  -F 'include_images=false'`;
 const protocol=location.protocol==='https:'?'wss':'ws';$('ws-example').textContent=`// 先 POST /api/camera/start 打开设备\nconst ws = new WebSocket(\n  '${protocol}://${location.host}/ws/camera'\n);\nws.onmessage = ({ data }) => {\n  const frame = JSON.parse(data);\n  if (frame.stale) return; // 清除旧位姿\n  for (const marker of frame.markers) {\n    if (marker.pose_status === 'valid')\n      console.log(marker.id, marker.pose.filtered?.camera_position_marker_cm ?? null);\n    else if (marker.pose_status === 'approximate')\n      console.warn('粗略相对位置', marker.id, marker.pose.filtered?.camera_position_marker_cm ?? null);\n  }\n};`;
 const endpoints=[['GET','/api/health','健康状态与 OpenCV 版本'],['GET','/api/family','完整基准族与编码定义'],['GET','/api/family/{id}/svg','按页面厘米尺寸打印标记 / 裁切模板'],['GET / PUT','/api/config','读取 / 保存检测参数'],['PUT / DELETE','/api/intrinsics','保存 / 删除相机内参'],['POST','/api/v1/detect','无状态单帧检测：图像 + 可选内参 + 可选参数'],['POST','/api/simulate','生成投影测试图并运行检测'],['POST','/api/camera/start','打开本机 UVC 相机'],['POST','/api/camera/stop','停止采集并释放设备'],['GET / PUT','/api/camera/controls','读取 / 设置硬件曝光与增益'],['POST','/api/camera/controls/reset','一键恢复硬件默认曝光'],['GET','/api/camera/frame','最新结果，可选附带画面'],['WS','/ws/camera','持续读取新鲜观测'],['POST / DELETE','/api/calibration/views','添加 / 清空棋盘格视图'],['POST','/api/calibration/observe','实时棋盘检测与自动稳定采集'],['POST','/api/calibration/camera-view','从后端相机采集棋盘视图'],['POST','/api/calibration/solve','计算内参及每视图误差']];endpoints.forEach(([method,path,description])=>{const row=document.createElement('div');row.innerHTML=`<strong>${method}</strong><code>${path}</code><span>${description}</span>`;$('endpoint-table').append(row)})
}
document.querySelectorAll('#view-modes button').forEach(b=>b.onclick=()=>{state.mode=b.dataset.mode;document.querySelectorAll('#view-modes button').forEach(x=>x.classList.toggle('selected',x===b));paint();refreshStreamMode()});
document.querySelectorAll('#source-tabs button').forEach(b=>b.onclick=()=>setSource(b.dataset.source).catch(e=>toast(e.message,true)));
on('show-overlay',paint,'change');on('show-unencoded',()=>{paint();refreshStreamMode()},'change');on('render-sim',renderSimulation);
on('fullscreen',async()=>{if(document.fullscreenElement)await document.exitFullscreen();else await $('viewport').requestFullscreen()});
on('capture-frame',()=>{if(!state.result)throw Error('当前没有可保存的图像。');$('viewer').toBlob(blob=>{const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`star-locator-${state.source}-${Date.now()}.png`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)},'image/png')});
on('export-result',()=>{if(!state.result)throw Error('当前没有可导出的观测。');const result={...state.result};delete result.images;download(`star-locator-observation-${Date.now()}.json`,JSON.stringify(result,null,2))});
on('start-browser',startBrowser);on('refresh-devices',refreshDevices);on('stop-browser',async()=>{await stopCapture();clearObservation('相机已断开');$('source-state').textContent='未连接';$('frame-rate').textContent='未连接'});
on('start-uvc',startUVC);on('stop-uvc',async()=>{await stopCapture();await api('/api/camera/stop',{method:'POST'});clearObservation('相机已断开');$('source-state').textContent='未连接';$('frame-rate').textContent='未连接'});
on('refresh-uvc-controls',refreshUvcControls);on('uvc-exposure-mode',()=>setUvcControlAvailability(true),'change');on('uvc-controls-form',applyUvcControls,'submit');on('reset-uvc-controls',resetUvcControls);
on('image-upload',async e=>{const file=e.target.files[0];if(file)await processFile(file)},'change');
on('cfg-threshold',()=>$('threshold-output').textContent=$('cfg-threshold').value,'input');on('cfg-threshold-mode',()=>$('cfg-threshold').disabled=$('cfg-threshold-mode').value!=='fixed','change');
on('config-form',async e=>{e.preventDefault();const response=await api('/api/config',{method:'PUT',...jsonOptions(formToConfig())});state.config=response.detector;$('config-status').textContent='参数已保存 · 后续帧立即生效';toast('检测参数已应用');await reprocess()},'submit');
on('reset-config',()=>{configToForm(state.defaultConfig);$('config-status').textContent='默认值已填入，点击“应用参数”生效。'});
on('download-family',()=>download('star16-v2-family.json',JSON.stringify(state.family,null,2)));
on('save-intrinsics',saveIntrinsics);on('intrinsics-file',async e=>{const file=e.target.files[0];if(file){const data=JSON.parse(await file.text());$('intrinsics-json').value=JSON.stringify(data.intrinsics||data,null,2);$('confirm-calibrated').checked=false;toast('已载入文件，请确认来源后保存。')}},'change');
on('export-intrinsics',()=>{const data=JSON.parse($('intrinsics-json').value);download('star-locator-camera-intrinsics.json',JSON.stringify(data,null,2))});
on('clear-intrinsics',async()=>{await api('/api/intrinsics',{method:'DELETE'});state.intrinsics=null;$('confirm-calibrated').checked=false;$('calibration-badge').textContent='尚未标定';toast('已清除内参，真实帧暂停输出厘米位姿。');await reprocess()});
document.querySelectorAll('[data-copy]').forEach(button=>button.onclick=()=>navigator.clipboard.writeText($(button.dataset.copy).textContent).then(()=>toast('已复制')).catch(()=>toast('复制失败，请手动选择示例。',true)));
setInterval(()=>{if(state.running&&state.lastLive&&performance.now()-state.lastLive>1800&&state.result)clearObservation('当前帧已过期，位姿已清除')},500);
window.addEventListener('beforeunload',()=>{state.stream?.getTracks().forEach(t=>t.stop());if(state.cameraOwned)fetch('/api/camera/stop',{method:'POST',keepalive:true}).catch(()=>{})});
async function init(){
 initApi();drawGrid($('mini-grid'),null);document.querySelectorAll('#view-modes button').forEach(b=>b.classList.toggle('selected',b.dataset.mode===state.mode));
 const health=await api('/api/health');$('service-text').textContent='算法服务已连接';$('opencv-version').textContent=`OpenCV ${health.opencv} · 本机处理`;
 const [family,config,calib]=await Promise.all([api('/api/family'),api('/api/config'),api('/api/calibration/status')]);state.family=family;state.config=config.detector;state.intrinsics=config.intrinsics;state.defaultConfig={spacing_mm:40,dot_diameter_mm:20,threshold_mode:'fixed',threshold:185,min_area:8,max_area:6000,min_circularity:.35,blur_size:3,max_hamming:0,grid_tolerance:.23,max_reprojection_error_px:2.5,edge_pose_max_reprojection_error_px:20,max_blobs:96,max_markers:8,temporal_smoothing_ms:300};initFamily(family);configToForm(config.detector);$('family-spacing').value=toCm(config.detector.spacing_mm);$('family-diameter').value=toCm(config.detector.dot_diameter_mm);
 const example={width:1280,height:720,fx:1000,fy:1000,cx:640,cy:360,distortion:[0,0,0,0,0],calibrated:false,source:'replace_with_real_calibration'};
 $('intrinsics-json').value=JSON.stringify(config.intrinsics||example,null,2);$('confirm-calibrated').checked=!!config.intrinsics?.calibrated;$('calibration-badge').textContent=config.intrinsics?.calibrated?'已保存真实内参':'尚未标定';calibrationUI=createCalibrationUI({api,on,toast,state});calibrationUI.updateStatus(calib,{restoreOptions:true});
 const requested=new URLSearchParams(location.search).get('source');
 if(requested==='simulation')await setSource('simulation');
 else{
  await setSource('uvc');
  if(health.camera.running){state.cameraOwned=false;$('source-state').textContent='后端 UVC 已在采集';connectWebSocket()}
  else{try{await startUVC()}catch(error){toast(error.message,true)}}
 }
 if(location.hash==='#calibration')await calibrationUI.setActive(true);
}
init().catch(error=>{$('service-dot').style.background='#e5a472';$('service-text').textContent='算法服务连接失败';clearObservation('算法服务尚未连接');toast(error.message,true)});
