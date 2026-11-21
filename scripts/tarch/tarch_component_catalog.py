"""Read installed T20 V10 component catalog metadata; does not create CAD entities."""
import argparse,collections,json,re,struct,sys,xml.etree.ElementTree as ET
from pathlib import Path

def dbf(path):
 b=path.read_bytes()
 if len(b)<33 or b[0] not in (3,0x83):raise ValueError('不支持的 dBase 格式: '+str(path))
 count=struct.unpack_from('<I',b,4)[0];header,size=struct.unpack_from('<HH',b,8)
 if header<33 or size<2 or header+count*size>len(b):raise ValueError('dBase 长度异常: '+str(path))
 columns=[];i=32
 while i<header and b[i]!=13:
  if i+32>header:raise ValueError('dBase 字段描述不完整')
  d=b[i:i+32];name=d[:11].split(b'\0')[0].decode('ascii');width=d[16]
  if not name or not width:raise ValueError('dBase 字段异常')
  columns.append((name,chr(d[11]),width));i+=32
 if i>=header or 1+sum(v[2] for v in columns)!=size:raise ValueError('dBase 记录长度不一致')
 active=[];deleted=0
 for j in range(count):
  row=b[header+j*size:header+(j+1)*size]
  if row[0]==42:deleted+=1;continue
  if row[0]!=32:raise ValueError('未知记录状态: '+str(j))
  values={};pos=1
  for name,typ,width in columns:
   value=row[pos:pos+width].strip().decode('gb18030',errors='replace');pos+=width
   values[name]=value
  active.append(values)
 return {'total':count,'active_count':len(active),'deleted_count':deleted,'fields':[x[0] for x in columns],'records':active}

def dwb_index(path):
 b=path.read_bytes();magic=b'ArchStar DWG  Library 1.0\r\n\x1a'
 if not b.startswith(magic) or len(b)<68:raise ValueError('图库头与测试版本不符: '+str(path))
 end=struct.unpack_from('<I',b,64)[0]
 if end<68 or end>len(b) or (end-32)%36:raise ValueError('图库目录边界异常: '+str(path))
 index={}
 for pos in range(32,end,36):
  raw=b[pos:pos+32].split(b'\0',1)[0];off=struct.unpack_from('<I',b,pos+32)[0]
  try:name=raw.decode('ascii')
  except UnicodeDecodeError:continue
  if name and name!='00000000' and re.fullmatch('[0-9A-Fa-f]{8}',name):
   if off<end or off>=len(b) or name in index:raise ValueError('图库索引异常: '+name)
   index[name]=off
 return index

def join_dbf_table(directory,table,library,idfield):
 data=dbf(directory/table);index=dwb_index(directory/library)
 matched=0;with_id=0
 for row in data['records']:
  code=row.get(idfield,'').strip();row['candidate_in_library_index']=bool(code and code in index)
  if code:with_id+=1
  if row['candidate_in_library_index']:matched+=1
 data.update({'table':str(directory/table),'library':str(directory/library),'library_index_count':len(index),'records_with_code':with_id,'code_matches_index':matched})
 return data

def main():
 ap=argparse.ArgumentParser(description='只读提取已安装天正构件索引；不能自动插入构件。')
 ap.add_argument('--install-root',default=r'C:\Tangent\TArchT20V10');ap.add_argument('--output',required=True)
 a=ap.parse_args();root=Path(a.install_root).resolve();d=root/'Dwb';out=Path(a.output).resolve()
 if out.exists():raise ValueError('输出已存在，不覆盖: '+str(out))
 if not out.parent.is_dir() or out.suffix.lower()!='.json':raise ValueError('输出须是现有目录内的新 JSON')
 if out.is_relative_to(root):raise ValueError('不允许写入天正安装目录')
 fixtures=join_dbf_table(d,'lvtry.DBF','lvtry.DWB','BLOCK')
 columns=join_dbf_table(d,'Column.tk','Column.DWB','DRAWING')
 windows=join_dbf_table(d,'WinLib2D.TK','WinLib2D.DWB','DRAWING')
 xml=root/'SYS'/'TCommonOpenning.xml';tree=ET.parse(xml);win=tree.getroot().find('窗')
 if win is None:raise ValueError('没有找到窗类型配置')
 presets=[]
 for category in win.findall('类别'):
  for item in category.findall('数据'):presets.append({'category':category.get('name',''),'attributes':dict(item.attrib)})
 data={'scope':'安装目录元数据；索引匹配不等于可成功插入或设计适用','fixtures':fixtures,'columns':columns,'window_2d_styles':windows,'window_presets':{'source':str(xml),'count':len(presets),'records':presets}}
 with out.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
 for title,z in [('洁具',fixtures),('柱图库',columns),('窗二维样式',windows)]:print(f"{title}: 总记录 {z['total']}，活动 {z['active_count']}，删除 {z['deleted_count']}，带编号 {z['records_with_code']}，索引候选匹配 {z['code_matches_index']}")
 print('窗参数预设:',len(presets));print('已写入:',out)
if __name__=='__main__':
 try:main()
 except Exception as e:print('目录读取失败:',e,file=sys.stderr);sys.exit(1)
