"""Isolated AutoCAD/T20 worker for the validated synthetic door and stair test."""
import collections, json, os, sys, time
from pathlib import Path
import pythoncom, win32com.client, win32process

SOURCE=Path(sys.argv[1]).resolve()
ROOT=Path(sys.argv[2]).resolve()
MODULES=['tch_kernal.arx','tch3_kernal.arx','tch_common.arx','tch_public.arx','tch_utility.arx','tch_kerncmd.arx','tch_uiframe.arx','tch_blklib.arx','tch_interior.arx','tch_tginterior.arx','TCH_UIPanelWindowAndDoor.arx','tch_3dsolids.arx','tch_annotate.arx','tch_collision.arx','tch_elevation.arx','tch_pipebase.arx','tch_pipewire.arx','tch_siteplan.arx','tch_structure.arx','tch_tgedit.arx','tch_tgtool.arx','tch_toolcmd.arx','tch_uipanelaxis.arx','TCH_UIPanelColumn.arx','tch_uipanellayer.arx','tch_uipanelredline.arx','tch_uipanelsymbol.arx','tch_uipanelsymbolmanage.arx','tch_uipanelwall.arx','tch_uitoolbar.arx']
MODULE_ROOT=Path(r'C:\Tangent\TArchT20V10\sys24x64')

def emit(tag,obj): print(tag+json.dumps(obj,ensure_ascii=False,default=str),flush=True)
def appobj(app,name):
 did=app._oleobj_.GetIDsOfNames(name)
 return win32com.client.Dispatch(app._oleobj_.Invoke(did,0,pythoncom.DISPATCH_PROPERTYGET,True))
def open_doc(app,path,readonly=False):
 docs=appobj(app,'Documents')
 try:return docs.Open(str(path),readonly)
 except AttributeError:
  did=docs._oleobj_.GetIDsOfNames('Open')
  return docs._oleobj_.Invoke(did,0,pythoncom.DISPATCH_METHOD,True,str(path),readonly)

def safe(o,k):
 try:
  x=getattr(o,k)
  return x if isinstance(x,(str,int,float,bool,type(None),tuple,list)) else str(x)
 except Exception as e:return 'ERROR:'+str(e)[:120]
def info(o):
 x={k:safe(o,k) for k in ('ObjectName','Handle','Layer')};name=x['ObjectName']
 if name=='TDbWall':
  for k in ('HoleArea','HoleVolume','Height','TotalWidth','IsArc'):x[k]=safe(o,k)
 elif name=='TDbOpening':
  for k in ('Width','Height','GetKind','GetSubKind','Label'):x[k]=safe(o,k)
 elif name=='TDbRectStair':
  for k in ('StairHeight','SpaceWidth','StairWidth','StepTotalNum'):x[k]=safe(o,k)
 try:x['bbox']=o.GetBoundingBox()
 except Exception as e:x['bbox_error']=str(e)[:100]
 return x

a=d=None;owned=False;osmode=None
pythoncom.CoInitialize()
try:
 before=set(win32process.EnumProcesses())
 a=win32com.client.DispatchEx('AutoCAD.Application.24.3')
 pid=win32process.GetWindowThreadProcessId(int(a.HWND))[1]
 if pid in before:raise RuntimeError('DispatchEx 返回已有 CAD 进程，拒绝操作')
 owned=True;emit('STEP:',{'cad_pid':pid})
 for i in range(35):
  try:
   profiles=win32com.client.Dispatch(win32com.client.Dispatch(a.Preferences).Profiles)
   profile=profiles.ActiveProfile;break
  except Exception:time.sleep(1)
 else:raise RuntimeError('配置接口未就绪')
 if profile!='TArch20V10':profiles.ActiveProfile='TArch20V10'
 try:a.LoadArx(str(MODULE_ROOT/'tch_initstart.arx'))
 except Exception:pass
 for i in range(45):
  try:
   if a.GetAcadState().IsQuiescent:break
  except Exception:pass
  time.sleep(1)
 else:raise RuntimeError('AutoCAD 启动超时')
 loaded=[]
 for name in MODULES:
  try:
   existing=[str(x).lower() for x in (a.ListArx() or [])]
   if name.lower() not in existing:a.LoadArx(str(MODULE_ROOT/name))
   loaded.append(name)
  except Exception as e:raise RuntimeError('模块加载失败 '+name+': '+str(e))
 time.sleep(2)
 for attempt in range(25):
  try:
   docs=appobj(a,'Documents');break
  except Exception:time.sleep(.5)
 else:raise RuntimeError('文档集合接口未就绪')
 open_doc(a,SOURCE,False)
 for i in range(40):
  try:
   d=appobj(a,'ActiveDocument')
   if Path(d.FullName).resolve()==SOURCE:break
  except Exception:pass
  time.sleep(.5)
 else:raise RuntimeError('隔离测试图未就绪')
 walls=sum(1 for x in d.ModelSpace if x.ObjectName=='TDbWall')
 if not walls:raise RuntimeError('源图没有可读取的天正墙')
 osmode=d.GetVariable('OSMODE')
 time.sleep(2)
 emit('READY:',{'pid':pid,'document':d.Name,'walls':walls,'units':d.GetVariable('INSUNITS'),'modules':len(loaded)})
 for line in sys.stdin:
  try:
   r=json.loads(line);op=r['op']
   if op=='status':
    for attempt in range(15):
     try:
      result={'cmd':d.GetVariable('CMDNAMES'),'objects':[info(x) for x in d.ModelSpace]};break
     except Exception as e:
      if '-2147418111' not in str(e) or attempt==14:raise
      time.sleep(.5)
   elif op=='command':
    name=r['name'];points=r['points']
    if name not in ('TOpening','TRStair'):raise ValueError('仅允许已验证的门和双跑楼梯命令')
    if not points:raise ValueError('点位为空')
    for attempt in range(20):
     try:
      if a.GetAcadState().IsQuiescent:break
     except Exception:pass
     time.sleep(.5)
    else:raise RuntimeError('命令前 CAD 未空闲')
    d.SetVariable('OSMODE',0)
    text=name+'\n'+''.join(f'{float(x):.8f},{float(y):.8f}\n' for x,y in points)+'\n'
    d.SendCommand(text)
    time.sleep(2)
    result={'cmd':d.GetVariable('CMDNAMES'),'objects':[info(x) for x in d.ModelSpace]}
   elif op=='elevator':
    points=r['points']
    if not points or any(len(group)!=4 for group in points):raise ValueError('每组需井道两角、门墙点及平衡块位置')
    d.SetVariable('OSMODE',0)
    text='TElevator\n'+''.join(''.join(f'{float(x):.8f},{float(y):.8f}\n' for x,y in group)+'\n' for group in points)+'\n'
    d.PostCommand(text)
    time.sleep(4)
    for attempt in range(35):
     try:
      if a.GetAcadState().IsQuiescent:
       result={'cmd':d.GetVariable('CMDNAMES'),'objects':[info(x) for x in d.ModelSpace]};break
     except Exception:pass
     time.sleep(1)
    else:raise RuntimeError('电梯命令仍等待输入或 CAD 忙碌')
   elif op=='set':
    o=d.HandleToObject(r['handle'])
    for k,v in r['values'].items():
     if k not in ('Width','Height','StairHeight','StepTotalNum'):raise ValueError('不支持属性 '+k)
     setattr(o,k,v)
    o.Update();result=info(o)
   elif op=='save':
    p=Path(r['path']).resolve()
    if not p.is_relative_to(ROOT/'output') or p.exists():raise ValueError('输出路径不在项目 output 内或已存在')
    d.SaveAs(str(p));result={'path':str(p),'bytes':p.stat().st_size}
   elif op=='reopen':
    p=Path(r['path']).resolve()
    if not p.is_relative_to(ROOT/'output') or not p.exists():raise ValueError('待重开文件不合法')
    d.Close(False);d=None
    open_doc(a,p,True)
    for i in range(30):
     try:
      d=appobj(a,'ActiveDocument')
      if Path(d.FullName).resolve()==p:break
     except Exception:pass
     time.sleep(.5)
    else:raise RuntimeError('重开未就绪')
    result={'readonly':bool(d.ReadOnly),'objects':[info(x) for x in d.ModelSpace]}
   elif op=='shutdown':emit('RESULT:',{'shutdown':True});break
   else:raise ValueError('不支持操作')
   emit('RESULT:',result)
  except Exception as e:emit('ERROR:',{'type':type(e).__name__,'message':str(e)[:400]})
except Exception as e:emit('FATAL:',{'type':type(e).__name__,'message':str(e)[:400]})
finally:
 if owned and a is not None:
  try:
   if d is not None and osmode is not None:d.SetVariable('OSMODE',osmode)
  except Exception:pass
  try:
   for doc in list(appobj(a,'Documents')):
    try:doc.Close(False)
    except Exception:pass
   a.Quit()
  except Exception:pass
 pythoncom.CoUninitialize()