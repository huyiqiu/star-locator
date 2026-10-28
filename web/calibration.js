export function createCalibrationUI({api,on,toast,state}) {
  const $=id=>document.getElementById(id);
  const ctx=$('calib-viewer').getContext('2d');
  const live={active:false,collecting:false,connecting:false,connected:false,token:0,ws:null,revision:null,views:0,solving:false,lastObservation:null,lastObservationAt:0,lastFrameAt:0,paint:0,options:null,completed:false,coverage:{cells:[],covered_cells:0,has_tilt_diversity:false}};
  const json=data=>({headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const options=()=>({columns:Number($('calib-cols').value),rows:Number($('calib-rows').value),square_mm:Number($('calib-square').value)*10});
  const target=()=>Number($('calib-target').value);
  const names={board_not_found:'等待完整棋盘',near_edge:'保留一些画面边距',board_too_small:'请靠近相机',blurred:'请缓慢移动并停稳',ready:'棋盘已检测',duplicate:'请换一个新视角',same_frame:'等待新鲜图像',target_reached:'采集目标已达到',limit_reached:'视图已满',hold_still:'请短暂停稳',cooldown:'准备采集',accepted:'已保存当前视角'};
  function feedback(title,message,warning=false){$('calib-feedback-title').textContent=title;$('calib-feedback-message').textContent=message;$('calib-feedback').classList.toggle('warning',warning);$('calib-feedback-icon').textContent=warning?'ⓘ':'◎'}
  function controls(){
    $('calib-auto-start').disabled=live.collecting||live.solving||live.connecting;
    $('calib-auto-stop').disabled=!live.collecting;
    $('solve-calibration').disabled=live.solving||live.views<8;
    $('capture-calibration').disabled=live.solving||live.connecting;
    ['calib-cols','calib-rows','calib-square','calib-target','calib-connect','calib-device-index','calib-resolution'].forEach(id=>$(id).disabled=live.collecting||live.solving||live.connecting);
    document.querySelectorAll('.calibration-steps>span').forEach((el,i)=>el.classList.toggle('active',i===(live.solving||live.completed?2:live.collecting?1:0)));
  }
  function updateStatus(status,{restoreOptions=false}={}){
    live.views=status.views??live.views;
    live.revision=status.revision??live.revision;
    if('options' in status)live.options=status.options;
    if(status.coverage)live.coverage=status.coverage;
    if(restoreOptions&&live.views&&live.options){
      $('calib-cols').value=live.options.columns;$('calib-rows').value=live.options.rows;$('calib-square').value=live.options.square_mm/10;checker();
      if(status.resolution){
        const value=status.resolution.join(','),select=$('calib-resolution');
        if(![...select.options].some(o=>o.value===value)){const option=document.createElement('option');option.value=value;option.textContent=status.resolution.join(' × ');select.append(option)}
        select.value=value;
      }
    }
    $('calibration-count').textContent=`${live.views} / ${target()} 个有效视图`;
    $('calib-progress-bar').style.width=`${Math.min(100,live.views/target()*100)}%`;
    const coverage=live.coverage;
    const grid=$('calib-coverage-grid');grid.replaceChildren();for(let i=0;i<9;i++){const cell=document.createElement('i');cell.classList.toggle('covered',coverage.cells.includes(i));grid.append(cell)}
    $('calib-coverage-label').textContent=`覆盖 ${coverage.covered_cells} / 9 区域`;
    $('calib-next-hint').textContent=live.views===0?'先拍正前方，再移向四角；将棋盘分别向上下、左右倾斜。':!coverage.has_tilt_diversity?'请增加上下、左右的倾斜角度；只平移或绕画面旋转棋盘不够。':coverage.covered_cells<6?'倾角已变化，再将棋盘移向画面四角，补充边缘区域。':'位置与倾角已覆盖多个区域，还可改变拍摄距离并继续停稳采集。';
    controls();
  }
  function checker(){const p=options();$('download-checker').href=`/api/calibration/chessboard.svg?columns=${p.columns}&rows=${p.rows}&square_mm=${p.square_mm}`;$('download-checker').textContent=`↓ 下载 ${p.columns} × ${p.rows} 内角点标定板`;$('checker-size').textContent=`${p.columns+1} × ${p.rows+1} 方格 · ${(p.columns+3)*p.square_mm/10} × ${(p.rows+3)*p.square_mm/10} cm`}
  function validOptions(){
    const p=options();if(!Number.isInteger(p.columns)||!Number.isInteger(p.rows)||p.columns<3||p.columns>20||p.rows<3||p.rows>20||p.square_mm<1||p.square_mm>200||!Number.isFinite(p.square_mm))throw Error('请输入 3–20 个内角点，以及 0.1–20 cm 的实际方格尺寸。');
    if(!Number.isInteger(target())||target()<8||target()>50)throw Error('目标视图数需为 8–50。');
    if(live.views&&live.options&&Object.keys(p).some(key=>p[key]!==live.options[key]))throw Error('棋盘规格已经改变，请先清空旧视图，再开始新的标定。');
    return p;
  }
  async function drawFrame(src,observation=null){
    const sequence=++live.paint;
    const image=new Image();image.src=src;await image.decode();
    if(!live.active||sequence!==live.paint)return;
    const canvas=$('calib-viewer');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.parentElement.style.aspectRatio=`${canvas.width}/${canvas.height}`;
    ctx.drawImage(image,0,0);$('calib-empty').hidden=true;
    const found=observation||(performance.now()-live.lastObservationAt<350?live.lastObservation:null);
    if(found?.found&&!observation){
      const points=found.corners_px,sx=canvas.width/found.width,sy=canvas.height/found.height;
      ctx.strokeStyle='#b6f578';ctx.fillStyle='#b6f578';ctx.lineWidth=Math.max(1,canvas.width/850);
      points.forEach(([x,y],i)=>{ctx.beginPath();ctx.arc(x*sx,y*sy,3.2*canvas.width/960,0,Math.PI*2);ctx.stroke();if(i%options().columns){const previous=points[i-1];ctx.beginPath();ctx.moveTo(previous[0]*sx,previous[1]*sy);ctx.lineTo(x*sx,y*sy);ctx.stroke()}});
    }
  }
  function closeStream(){if(live.ws){live.ws.onmessage=live.ws.onclose=live.ws.onerror=null;live.ws.close();live.ws=null}}
  function connectStream(){
    closeStream();const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/camera?image_mode=raw`);live.ws=ws;
    ws.onmessage=event=>{const frame=JSON.parse(event.data);if(!live.active)return;if(frame.stale){live.connected=false;$('calib-camera-status').textContent='等待新鲜图像';return}if(frame.images?.raw){live.connected=true;live.lastFrameAt=performance.now();$('calib-camera-status').textContent=`${frame.width} × ${frame.height} · USB`;drawFrame(frame.images.raw).catch(()=>{})}};
    ws.onclose=()=>{live.connected=false;live.collecting=false;controls();$('calib-camera-status').textContent='相机已断开';if(live.active)feedback('相机已断开','请点击“连接 / 重新连接”恢复实时标定。',true)};
    ws.onerror=()=>{if(live.active)$('calib-camera-status').textContent='预览连接异常'};
  }
  function addSample(result){
    const img=document.createElement('img');img.src=result.thumbnail||result.preview;img.alt=`第 ${result.views} 个棋盘视图`;img.title=`视图 ${result.views} · 清晰度 ${result.quality?.sharpness.toFixed(0)||'—'}`;$('calib-samples').append(img);$('calib-samples').scrollLeft=$('calib-samples').scrollWidth;
    $('calibration-preview').src=result.preview;$('calibration-preview').hidden=false;
  }
  async function solve(){
    if(live.solving)return;live.collecting=false;live.solving=true;controls();
    feedback('正在计算相机内参','使用所有已采集的视图求解，请稍候。');
    try{
      const result=await api('/api/calibration/solve',{method:'POST'});
      if(live.revision!==null&&result.revision!==live.revision)return;
      live.completed=true;$('intrinsics-json').value=JSON.stringify(result.intrinsics,null,2);$('confirm-calibrated').checked=true;$('calibration-badge').textContent='计算完成 · 待应用';
      $('calib-result-summary').hidden=false;
      $('calib-result-rms').replaceChildren(document.createTextNode(result.intrinsics.rms_px.toFixed(3)+' '));const unit=document.createElement('small');unit.textContent='px';$('calib-result-rms').append(unit);
      const warningLabels={high_calibration_rms:'重投影误差偏大',insufficient_tilt_diversity:'倾斜视角变化不足',limited_image_coverage:'画面边缘覆盖不足'};
      const warning=result.warnings.map(w=>warningLabels[w]||w);
      $('calib-result-quality').textContent=warning.length?`建议补拍：${warning.join('；')}。`:`已使用 ${result.views} 个视图，覆盖 ${result.coverage.covered_cells}/9 个图像区域。请检查误差后应用。`;
      $('calib-result-quality').classList.toggle('warning',warning.length>0);
      const errors=$('calib-per-view-errors');errors.replaceChildren();result.per_view_rms_px.forEach((v,i)=>{const e=document.createElement('span');e.textContent=`${i+1}: ${v.toFixed(2)}`;e.title=`视图 ${i+1} 重投影误差 ${v.toFixed(3)} px`;errors.append(e)});
      $('calibration-solve-note').textContent=`已完成 ${result.views} 个视图的计算。检查右侧结果，点击“应用标定结果”生效。`;
      feedback('标定计算完成',warning.length?`建议补拍：${warning.join('；')}，也可检查右侧结果。`:'请检查右侧重投影误差，然后点击“应用标定结果”。',warning.length>0);
      toast(warning.length?'已计算内参，部分采集质量需要检查。':'标定计算完成，可应用内参。',warning.length>0);
    }finally{live.solving=false;controls()}
  }
  function observeLoop(){
    const token=++live.token;
    async function tick(){
      if(!live.active||token!==live.token)return;
      if(live.solving){setTimeout(tick,400);return}
      try{
        const p=options();
        const result=await api('/api/calibration/observe',{method:'POST',...json({...p,collect:live.collecting,target_views:target(),session_revision:live.revision})});
        if(token!==live.token||!live.active)return;
        live.connected=true;live.lastObservation=result;live.lastObservationAt=performance.now();
        updateStatus(result);$('calib-corner-count').textContent=result.found?`${result.corners_px.length} / ${p.columns*p.rows} 角点${result.stable?' · 稳定':''}`:'等待棋盘';
        if(!live.solving&&!live.completed)feedback(names[result.reason]||'棋盘检测',result.message,['blurred','near_edge','board_too_small'].includes(result.reason));
        if(performance.now()-live.lastFrameAt>500)await drawFrame(result.preview,result);
        if(result.accepted)addSample(result);
        if(live.collecting&&result.target_reached){live.collecting=false;controls();await solve()}
      }catch(error){if(token!==live.token||!live.active)return;feedback('等待有效标定视图',error.message,true);if(live.collecting){live.collecting=false;controls()}}
      if(live.active&&token===live.token)setTimeout(tick,350);
    }
    tick();
  }
  async function connectCamera(){
    if(live.connecting)return;
    validOptions();
    live.connecting=true;controls();feedback('正在连接 USB 相机','正在等待设备的第一帧图像，请稍候。');
    try{
    if(state.stream){state.running=false;state.stream.getTracks().forEach(t=>t.stop());state.stream=null;$('camera-video').srcObject=null}
    const [width,height]=$('calib-resolution').value.split(',').map(Number);
    if(live.views&&live.options){const status=await api('/api/calibration/status');if(status.resolution&&(status.resolution[0]!==width||status.resolution[1]!==height))throw Error('分辨率与已有视图不同，请先清空视图。')}
    const status=await api('/api/camera/start',{method:'POST',...json({index:Number($('calib-device-index').value),width,height,fps:30})});
    live.connected=true;$('calib-camera-status').textContent=`${status.device.width} × ${status.device.height} · USB`;
    connectStream();observeLoop();feedback('相机已连接','将完整棋盘放入画面，点击“开始自动采集”。');
    }finally{live.connecting=false;controls()}
  }
  async function setActive(active){
    if(live.active===active)return;live.active=active;
    if(!active){live.collecting=false;live.token++;closeStream();controls();return}
    const [health,status]=await Promise.all([api('/api/health'),api('/api/calibration/status')]);
    if(!live.active)return;updateStatus(status,{restoreOptions:true});
    if(health.camera.running){live.connected=true;$('calib-device-index').value=health.camera.device.index;const value=`${health.camera.device.width},${health.camera.device.height}`;if([...$('calib-resolution').options].some(o=>o.value===value))$('calib-resolution').value=value;connectStream();observeLoop()}
    else if(live.views)feedback(`已恢复 ${live.views} 个标定视图`,'棋盘规格与分辨率已恢复。可直接计算内参，也可连接相机继续补拍。');
    else feedback('连接 USB 相机','点击“连接 / 重新连接”，即可在本页手持棋盘完成标定。');
  }
  on('calib-connect',connectCamera);
  on('calib-auto-start',async()=>{
    live.completed=false;validOptions();if(live.views>=50)throw Error('已有 50 个视图，请计算结果或清空重采。');
    if(live.views>=target())$('calib-target').value=Math.min(50,live.views+5);
    const health=await api('/api/health');if(!health.camera.running)await connectCamera();
    const status=await api('/api/calibration/status');updateStatus(status);live.collecting=true;controls();
    feedback('自动采集中','每换一个视角停稳约半秒；请覆盖画面四角并改变倾角。');
    if(!live.ws)connectStream();observeLoop();
  });
  on('calib-auto-stop',()=>{live.collecting=false;controls();feedback('已暂停采集','实时预览继续运行，可继续采集或计算已有视图。')});
  on('capture-calibration',async()=>{const result=await api('/api/calibration/camera-view',{method:'POST',...json(validOptions())});updateStatus(result);addSample(result);feedback('已采集这一帧',result.message)});
  on('solve-calibration',solve);
  on('reset-calibration',async()=>{live.collecting=false;live.token++;const status=await api('/api/calibration/views',{method:'DELETE'});live.options=null;live.completed=false;live.lastObservation=null;updateStatus(status);$('calib-samples').replaceChildren();$('calibration-log').textContent='';$('calibration-preview').hidden=true;$('calib-result-summary').hidden=true;$('confirm-calibrated').checked=false;if(live.active&&live.connected)observeLoop();feedback('视图已清空','可以设置新的棋盘规格，开始下一次采集。')});
  on('calibration-files',async event=>{
    live.collecting=false;controls();const errors=[];
    for(const file of event.target.files){const form=new FormData();form.append('file',file);for(const [key,value] of Object.entries(validOptions()))form.append(key,String(value));try{const result=await api('/api/calibration/views',{method:'POST',body:form});updateStatus(result);addSample(result)}catch(e){errors.push(`${file.name}: ${e.message}`)}}
    $('calibration-log').textContent=errors.length?errors.join('\n'):'照片已加入标定视图。';event.target.value='';
  },'change');
  ['calib-cols','calib-rows','calib-square'].forEach(id=>on(id,()=>{checker();if(live.views)feedback('棋盘规格已修改','请先清空旧视图，再使用新的棋盘规格采集。',true)},'input'));
  on('calib-target',()=>updateStatus({}),'input');
  checker();updateStatus({views:0});
  return {setActive,updateStatus};
}
