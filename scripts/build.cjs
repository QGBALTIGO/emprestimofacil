const {spawnSync}=require('node:child_process');
const path=require('node:path');
const python=process.env.PYTHON||(process.platform==='win32'?'python':'python3');
const result=spawnSync(python,['-X','utf8',path.join(__dirname,'build-reference.py')],{stdio:'inherit',cwd:path.join(__dirname,'..')});
if(result.error){process.stderr.write('Não foi possível executar Python para construir o site.\n');process.exit(1);}
process.exit(result.status===null?1:result.status);
