"""Demonstration-only T20 V10 native door + double-stair batch runner.
Input/output are explicit; never overwrites a DWG and never modifies the source.
"""
import argparse, hashlib, json, os, queue, shutil, subprocess, sys, tempfile, threading, time, winreg
from pathlib import Path

REG=r'Software\Tangent\TArch\20V10\Settings\Door'

def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def read_records(path):
 d=json.loads(path.read_text(encoding='utf-8'))
 for key in ('doors','stairs'):
  if not isinstance(d.get(key),list) or not d[key]:raise ValueError(key+' 必须为非空列表')
 for x in d['doors']:
  if len(x['point'])!=2 or not x.get('wall') or float(x['width'])<=0 or float(x['height'])<=0:raise ValueError('门参数无效')
 for x in d['stairs']:
  if len(x['point'])!=2 or float(x['height'])<=0 or int(x['steps'])<=0:raise ValueError('楼梯参数无效')
 return d
def inside(pt,bbox):
 (x,y)=map(float,pt);(lo,hi)=bbox
 return float(lo[0])-0.01<=x<=float(hi[0])+0.01 and float(lo[1])-0.01<=y<=float(hi[1])+0.01
def wall_accepts(pt,w,width):
 if w.get('IsArc') not in (False,0,'直墙'):return False
 lo,hi=w['bbox']; x,y=map(float,pt);ax=float(hi[0])-float(lo[0]);ay=float(hi[1])-float(lo[1]);tol=0.05;margin=float(width)/2+5
 if ax>ay*2:return (abs(y-float(lo[1]))<=tol or abs(y-float(hi[1]))<=tol) and float(lo[0])+margin<=x<=float(hi[0])-margin
 if ay>ax*2:return (abs(x-float(lo[0]))<=tol or abs(x-float(hi[0]))<=tol) and float(lo[1])+margin<=y<=float(hi[1])-margin
 return False

def main():
 parser=argparse.ArgumentParser(description='天正 V10 原生门与双跑楼梯批量示例；先在独立图测试。')
 parser.add_argument('--project',required=True);parser.add_argument('--source',required=True)
 parser.add_argument('--spec',required=True);parser.add_argument('--output',required=True)
 args=parser.parse_args()
 root=Path(args.project).resolve();src=Path(args.source).resolve();spec=Path(args.spec).resolve();out=Path(args.output).resolve()
 if not src.is_file() or not spec.is_file():raise ValueError('源图或参数文件不存在')
 if not out.is_relative_to(root/'output') or out.suffix.lower()!='.dwg':raise ValueError('输出必须位于项目 output 内且为 DWG')
 if out.exists() or out.with_suffix('.json').exists():raise ValueError('输出或报告已存在，拒绝覆盖')
 if not out.parent.is_dir() or not (root/'tmp').is_dir():raise ValueError('输出或临时目录不存在')
 data=read_records(spec);original_hash=digest(src)
 work=Path(tempfile.mkdtemp(prefix='native_batch_',dir=root/'tmp'));scratch=work/'input.dwg';shutil.copy2(src,scratch)
 worker=Path(__file__).with_name('native_batch_worker_v5.py')
 if not worker.is_file():raise ValueError('缺少同目录 native_batch_worker.py')
 with winreg.OpenKey(winreg.HKEY_CURRENT_USER,REG,0,winreg.KEY_QUERY_VALUE|winreg.KEY_SET_VALUE) as k:
  old,kind=winreg.QueryValueEx(k,'InsertMode')
  if kind!=winreg.REG_DWORD:raise ValueError('门定位设置类型异常')
  if old!=0:winreg.SetValueEx(k,'InsertMode',0,kind,0)
 proc=None;cad_pid=None;q=queue.Queue();history=[];source_ok=False
 try:
  env=os.environ.copy();env['PYTHONIOENCODING']='utf-8'
  proc=subprocess.Popen([sys.executable,'-u',str(worker),str(scratch),str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',bufsize=1,env=env)
  def reader():
   for line in proc.stdout:q.put(line.rstrip('\r\n'))
   q.put(None)
  threading.Thread(target=reader,daemon=True).start()
  def receive(wanted,timeout=60):
   nonlocal cad_pid
   end=time.monotonic()+timeout
   while time.monotonic()<end:
    try:line=q.get(timeout=min(2,max(.1,end-time.monotonic())))
    except queue.Empty:
     if proc.poll() is not None:raise RuntimeError('天正工作器提前退出')
     continue
    if line is None:raise RuntimeError('天正工作器输出中断')
    if line.startswith('STEP:'):
     msg=json.loads(line[5:]);cad_pid=msg.get('cad_pid',cad_pid);print('隔离 CAD 进程',cad_pid,flush=True);continue
    for prefix in ('READY:','RESULT:','ERROR:','FATAL:'):
     if line.startswith(prefix):
      val=json.loads(line[len(prefix):])
      if prefix in ('ERROR:','FATAL:'):raise RuntimeError(str(val))
      if prefix==wanted:return val
   raise TimeoutError('等待 '+wanted+' 超时')
  def call(value,timeout=35):
   proc.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');proc.stdin.flush();return receive('RESULT:',timeout)
  ready=receive('READY:',80)
  if ready['units']!=4:raise ValueError('仅支持单位为毫米的演示图')
  print('天正已加载，墙体数',ready['walls'],flush=True)
  print('读取墙体与洞口状态',flush=True)
  initial=call({'op':'status'})['objects'];old_handles={x['Handle'] for x in initial}
  walls={x['Handle']:x for x in initial if x['ObjectName']=='TDbWall'}
  changes={}
  for item in data['doors']:
   w=walls.get(item['wall'])
   if not w or not wall_accepts(item['point'],w,item['width']):raise ValueError('门点位未落在指定正交墙边或距墙端过近：'+str(item))
   changes[item['wall']]=changes.get(item['wall'],0)+float(item['width'])*float(item['height'])/1e6
  print('开始批量插门',flush=True)
  result=call({'op':'command','name':'TOpening','points':[x['point'] for x in data['doors']]},35)
  new=[x for x in result['objects'] if x['ObjectName']=='TDbOpening' and x['Handle'] not in old_handles]
  if len(new)!=len(data['doors']):raise RuntimeError('新门数量不符：'+str(len(new)))
  used=set();doors=[]
  for item in data['doors']:
   matches=[x for x in new if x['Handle'] not in used and inside(item['point'],x['bbox'])]
   if len(matches)!=1:raise RuntimeError('门与输入点位对应不唯一：'+str(item['point']))
   handle=matches[0]['Handle'];used.add(handle)
   info=call({'op':'set','handle':handle,'values':{'Width':float(item['width']),'Height':float(item['height'])}})
   if info['GetKind']!='普通门' or abs(float(info['Width'])-float(item['width']))>.01 or abs(float(info['Height'])-float(item['height']))>.01:raise RuntimeError('门属性回读不一致')
   doors.append({'handle':handle,'wall':item['wall'],'point':item['point'],'width':info['Width'],'height':info['Height']})
  after=call({'op':'status'})['objects'];wall_after={x['Handle']:x for x in after if x['ObjectName']=='TDbWall'}
  for handle,area in changes.items():
   delta=float(wall_after[handle]['HoleArea'])-float(walls[handle]['HoleArea'])
   if abs(delta-area)>.02:raise RuntimeError('墙洞面积不符：'+handle+' 实际 '+str(delta)+' 期望 '+str(area))
  print('原生门已创建并核对墙洞：',len(doors),flush=True)
  before_stairs={x['Handle'] for x in after}
  result=call({'op':'command','name':'TRStair','points':[x['point'] for x in data['stairs']]},35)
  new=[x for x in result['objects'] if x['ObjectName']=='TDbRectStair' and x['Handle'] not in before_stairs]
  if len(new)!=len(data['stairs']):raise RuntimeError('新楼梯数量不符：'+str(len(new)))
  used=set();stairs=[]
  for item in data['stairs']:
   matches=[x for x in new if x['Handle'] not in used and inside(item['point'],x['bbox'])]
   if len(matches)!=1:raise RuntimeError('楼梯与输入点位对应不唯一：'+str(item['point']))
   handle=matches[0]['Handle'];used.add(handle)
   call({'op':'set','handle':handle,'values':{'StairHeight':float(item['height'])}})
   info=call({'op':'set','handle':handle,'values':{'StepTotalNum':int(item['steps'])}})
   if abs(float(info['StairHeight'])-float(item['height']))>.01 or int(info['StepTotalNum'])!=int(item['steps']):raise RuntimeError('楼梯参数回读不一致')
   stairs.append({'handle':handle,'height':info['StairHeight'],'steps':info['StepTotalNum'],'point':item['point']})
  print('原生双跑楼梯已创建：',len(stairs),flush=True)
  call({'op':'save','path':str(out)},40)
  checked=call({'op':'reopen','path':str(out)},50)
  if not checked['readonly']:raise RuntimeError('复核图不是只读打开')
  by_handle={x['Handle']:x for x in checked['objects']}
  for door in doors:
   o=by_handle[door['handle']]
   if o['ObjectName']!='TDbOpening' or o['GetKind']!='普通门' or abs(float(o['Width'])-door['width'])>.01 or abs(float(o['Height'])-door['height'])>.01:raise RuntimeError('重开后门属性不符')
  for stair in stairs:
   o=by_handle[stair['handle']]
   if o['ObjectName']!='TDbRectStair' or abs(float(o['StairHeight'])-stair['height'])>.01 or int(o['StepTotalNum'])!=stair['steps']:raise RuntimeError('重开后楼梯属性不符')
  for handle,area in changes.items():
   delta=float(by_handle[handle]['HoleArea'])-float(walls[handle]['HoleArea'])
   if abs(delta-area)>.02:raise RuntimeError('重开后墙洞面积不符：'+handle)
  source_ok=digest(src)==original_hash
  if not source_ok:raise RuntimeError('源文件哈希变化')
  report={'scope':'合成示意图；非工程尺寸','source':str(src),'source_sha256':original_hash,'source_unchanged':source_ok,'output':str(out),'doors':doors,'stairs':stairs,'wall_hole_area_delta_m2':changes,'reopened_readonly':True,'limitations':['仅普通门和双跑楼梯；未验证窗、柱、洁具和批量电梯','不自动识别扫描图','斜墙、弧墙及复杂墙交接未验证']}
  with out.with_suffix('.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
  print('重开核验通过：',out,flush=True)
 finally:
  if proc is not None:
   try:
    if proc.poll() is None:
     proc.stdin.write('{"op":"shutdown"}\n');proc.stdin.flush();proc.wait(timeout=15)
   except Exception:
    try:proc.terminate();proc.wait(timeout=5)
    except Exception:pass
  with winreg.OpenKey(winreg.HKEY_CURRENT_USER,REG,0,winreg.KEY_QUERY_VALUE|winreg.KEY_SET_VALUE) as k:
   now,nowkind=winreg.QueryValueEx(k,'InsertMode')
   if now==0 and old!=0:winreg.SetValueEx(k,'InsertMode',0,kind,old)
   elif now not in (0,old):print('警告：门定位设置已由其他程序修改，未覆盖。',file=sys.stderr,flush=True)
  print('门定位设置恢复完成；临时文件保留于',work,flush=True)
if __name__=='__main__':
 try:main()
 except Exception as e:print('批量示例失败：',str(e),file=sys.stderr,flush=True);sys.exit(1)