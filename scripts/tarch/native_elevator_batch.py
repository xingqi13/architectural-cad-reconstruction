"""T20 V10 elevator diagrams and hosted elevator doors: synthetic batch prototype."""
import argparse,hashlib,json,os,queue,shutil,subprocess,sys,tempfile,threading,time
from pathlib import Path

def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def inside(point,bbox):
 (x,y)=map(float,point);lo,hi=bbox
 return float(lo[0])-.05<=x<=float(hi[0])+.05 and float(lo[1])-.05<=y<=float(hi[1])+.05
def main():
 ap=argparse.ArgumentParser(description='天正 V10 电梯门和简图批量示例；限在独立测试图验证。')
 for name in ('project','source','spec','output'):ap.add_argument('--'+name,required=True)
 a=ap.parse_args();root=Path(a.project).resolve();src=Path(a.source).resolve();spec=Path(a.spec).resolve();out=Path(a.output).resolve()
 if not src.is_file() or not spec.is_file():raise ValueError('缺少源文件或参数')
 if not out.is_relative_to(root/'output') or out.suffix.lower()!='.dwg' or not out.parent.is_dir():raise ValueError('输出须位于项目 output 的现有目录')
 if out.exists() or out.with_suffix('.json').exists():raise ValueError('输出已存在，不覆盖')
 if not (root/'tmp').is_dir():raise ValueError('缺少项目 tmp 目录')
 items=json.loads(spec.read_text(encoding='utf-8')).get('elevators')
 if not isinstance(items,list) or not items:raise ValueError('elevators 不能为空')
 groups=[]
 for e in items:
  points=list(e['corners'])+[e['door_wall_point'],e['counterweight_point']]
  if len(points)!=4 or any(len(x)!=2 for x in points) or not e.get('wall'):raise ValueError('每组需两角、门墙点、平衡块点及墙句柄')
  groups.append(points)
 original=digest(src);work=Path(tempfile.mkdtemp(prefix='elevator_batch_',dir=root/'tmp'));scratch=work/'input.dwg';shutil.copy2(src,scratch)
 worker=Path(__file__).with_name('native_elevator_worker_v4.py')
 if not worker.is_file():raise ValueError('缺少同目录工作器')
 env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';p=subprocess.Popen([sys.executable,'-u',str(worker),str(scratch),str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',bufsize=1,env=env)
 q=queue.Queue();pid=None
 def pump():
  for line in p.stdout:q.put(line.strip())
  q.put(None)
 threading.Thread(target=pump,daemon=True).start()
 def recv(wanted,timeout):
  nonlocal pid
  end=time.monotonic()+timeout
  while time.monotonic()<end:
   try:line=q.get(timeout=1)
   except queue.Empty:continue
   if line is None:raise RuntimeError('天正工作器提前退出')
   for tag in ('STEP:','READY:','RESULT:','ERROR:','FATAL:'):
    if line.startswith(tag):
     data=json.loads(line[len(tag):])
     if tag=='STEP:':pid=data.get('cad_pid');print('隔离 CAD 进程:',pid,flush=True)
     if tag in ('ERROR:','FATAL:'):raise RuntimeError(str(data))
     if tag==wanted:return data
  raise TimeoutError('等待 '+wanted+' 超时')
 def call(req,timeout=65):p.stdin.write(json.dumps(req,ensure_ascii=False)+'\n');p.stdin.flush();return recv('RESULT:',timeout)
 try:
  ready=recv('READY:',100)
  if ready['units']!=4:raise ValueError('仅支持毫米示例')
  before=call({'op':'status'});objects=before['objects'];previous={x['Handle'] for x in objects};walls={x['Handle']:x for x in objects if x['ObjectName']=='TDbWall'}
  for e in items:
   if e['wall'] not in walls or not inside(e['door_wall_point'],walls[e['wall']]['bbox']):raise ValueError('门墙点不在指定墙范围内：'+e['wall'])
  print('开始批量投递电梯坐标，数量:',len(items),flush=True)
  after=call({'op':'elevator','points':groups});new=[x for x in after['objects'] if x['Handle'] not in previous]
  doors=[x for x in new if x['ObjectName']=='TDbOpening' and x.get('GetSubKind')=='电梯门'];evtr=[x for x in new if x['ObjectName']=='AcDbLine' and x['Layer'].upper()=='EVTR']
  if after['cmd'] or len(doors)!=len(items) or len(evtr)!=10*len(items):raise RuntimeError('电梯门、简图线数量或退出状态不符')
  used=set();records=[];area={}
  for e in items:
   matches=[x for x in doors if x['Handle'] not in used and inside(e['door_wall_point'],x['bbox'])]
   if len(matches)!=1:raise RuntimeError('电梯门和输入点无法唯一对应')
   d=matches[0];used.add(d['Handle']);expected=float(d['Width'])*float(d['Height'])/1000000.0;area[e['wall']]=area.get(e['wall'],0)+expected
   records.append({'wall':e['wall'],'door_handle':d['Handle'],'width':d['Width'],'height':d['Height'],'door_wall_point':e['door_wall_point']})
  by={x['Handle']:x for x in after['objects']}
  for wall,expected in area.items():
   if abs(float(by[wall]['HoleArea'])-float(walls[wall]['HoleArea'])-expected)>.02:raise RuntimeError('电梯墙洞面积不符:'+wall)
  call({'op':'save','path':str(out)});check=call({'op':'reopen','path':str(out)});by={x['Handle']:x for x in check['objects']}
  if not check['readonly']:raise RuntimeError('重开不是只读')
  for rec in records:
   d=by[rec['door_handle']]
   if d.get('GetSubKind')!='电梯门' or abs(float(d['Width'])-float(rec['width']))>.01 or abs(float(d['Height'])-float(rec['height']))>.01:raise RuntimeError('电梯门重开不一致')
  for wall,expected in area.items():
   if abs(float(by[wall]['HoleArea'])-float(walls[wall]['HoleArea'])-expected)>.02:raise RuntimeError('重开墙洞面积不符:'+wall)
  if any(by[x['Handle']]['Layer'].upper()!='EVTR' for x in evtr):raise RuntimeError('简图线重开不一致')
  if digest(src)!=original:raise RuntimeError('源图哈希发生变化')
  report={'scope':'合成示意图，非工程尺寸','source':str(src),'source_sha256':original,'source_unchanged':True,'output':str(out),'elevators':records,'wall_hole_area_delta_m2':area,'elevator_schematic_line_count':len(evtr),'reopened_readonly':True,'limitations':['电梯为门对象与二维简图线，不是完整厂家设备或井道模型','实际井道墙厚、门方向、参数及候梯空间须单独核验','只验证简单正交示例']}
  with out.with_suffix('.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
  print('保存并只读重开核验通过:',out,flush=True)
 finally:
  try:
   if p.poll() is None:p.stdin.write('{"op":"shutdown"}\n');p.stdin.flush();p.wait(timeout=15)
  except Exception:
   p.terminate()
   try:p.wait(timeout=5)
   except Exception:pass
  print('临时文件:',work,flush=True)
if __name__=='__main__':
 try:main()
 except Exception as e:print('电梯批量示例失败:',str(e),file=sys.stderr,flush=True);sys.exit(1)
