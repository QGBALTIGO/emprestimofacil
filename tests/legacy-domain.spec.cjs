const {test,expect}=require('@playwright/test'),fs=require('node:fs'),path=require('node:path');
test('link antigo abre a VPS e preserva o cadastro PF sem enviar dados',async({page})=>{
 const markup=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');let submissions=0;
 await page.route('https://qgbaltigo.github.io/emprestimofacil/',route=>route.fulfill({contentType:'text/html',body:markup}));
 await page.route('https://emprestimofacil.pro/**',route=>{
   const url=new URL(route.request().url());if(route.request().method()==='POST')submissions++;
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:markup});
   if(url.pathname.startsWith('/assets/')){
     const file=path.join(__dirname,'..',url.pathname),ext=path.extname(file);
     if(fs.existsSync(file))return route.fulfill({contentType:({'.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml','.woff2':'font/woff2','.woff':'font/woff','.css':'text/css'})[ext]||'application/octet-stream',body:fs.readFileSync(file)});
   }
   return route.fulfill({contentType:'application/json',body:'{}'});
 });
 await page.goto('https://qgbaltigo.github.io/emprestimofacil/#cadastro-pf');
 await expect(page).toHaveURL('https://emprestimofacil.pro/#cadastro-pf');
 await expect(page.locator('#pf-email')).toBeVisible();expect(submissions).toBe(0);
});
