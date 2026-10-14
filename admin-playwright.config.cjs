const {defineConfig}=require('@playwright/test');
const fs=require('node:fs');
const path=require('node:path');
const sizes=[[320,740],[360,800],[390,844],[412,915],[640,900],[768,1024],[1024,768],[1280,800],[1440,900],[1920,1080],[2560,1440]];
const browsers=process.env.TEST_BROWSER?[process.env.TEST_BROWSER]:['chromium','firefox','webkit'];
const python=process.platform==='win32'?path.join(__dirname,'.venv','Scripts','python.exe'):(fs.existsSync('.venv/bin/python')?path.join(__dirname,'.venv','bin','python'):'python3');
module.exports=defineConfig({testDir:'./tests',testMatch:'admin-portal.spec.cjs',timeout:60000,globalTimeout:8*60*1000,workers:2,retries:0,
  outputDir:'test-results/admin-portal',reporter:[['list'],['html',{open:'never',outputFolder:'playwright-report/admin'}],['json',{outputFile:'test-results/admin-results.json'}]],
  use:{baseURL:'https://127.0.0.1:4187',ignoreHTTPSErrors:true,trace:'retain-on-failure',screenshot:'only-on-failure'},
  projects:browsers.flatMap(browserName=>sizes.map(([width,height])=>({name:browserName+'-'+width,use:{browserName,viewport:{width,height},hasTouch:width<1100,isMobile:browserName!=='firefox'&&width<700,deviceScaleFactor:1,colorScheme:'light'}}))),
  webServer:{command:'"'+python+'" tests/client_fixture_server.py --port 4187 --ready-file test-results/admin-browser-fixture.json',url:'https://127.0.0.1:4187/api/health',ignoreHTTPSErrors:true,reuseExistingServer:false,timeout:240000}});
