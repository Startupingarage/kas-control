import {test,expect} from '@playwright/test';

async function startup(page:any,fail=false){
 await page.addInitScript((fail:boolean)=>{
  const w=window as any;w.__legacySeen=false;
  new MutationObserver(()=>{if(document.querySelector('.app > .sidebar'))w.__legacySeen=true;}).observe(document,{childList:true,subtree:true});
  w.__fail=fail;w.__release=()=>{};
  const data={project_sessions:true,projects:[{id:'core',name:'Мой проект'}],current:{project_id:'core',phase:'idle',voice:'orbit'},router_ready:true,routes:[],native_commands:[]};
  w.pywebview={api:{bootstrap:()=>new Promise(resolve=>{w.__release=()=>resolve(w.__fail?{ok:false,error:'Нет связи с колонкой.'}:{ok:true,data});}),archive_status:async()=>({ok:true,data:{running:false}})}};
 },fail);
 await page.goto('/');
}

test('pending startup never paints the legacy interface',async({page})=>{
 await startup(page);
 await expect(page.getByRole('heading',{name:'Подключаемся к колонке'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Новый чат',exact:true})).toHaveCount(0);
 await page.screenshot({path:'screenshots/friendly-startup.png'});
 await page.evaluate(()=>(window as any).__release());
 await expect(page.getByRole('heading',{name:'Мой проект',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>(window as any).__legacySeen)).toBe(false);
});

test('failed startup keeps a clear connection screen and can retry',async({page})=>{
 await startup(page,true);await expect(page.getByRole('heading',{name:'Подключаемся к колонке'})).toBeVisible();
 await page.evaluate(()=>(window as any).__release());
 await expect(page.getByRole('alert')).toHaveText('Нет связи с колонкой.');
 await expect(page.getByRole('button',{name:'Повторить подключение'})).toBeVisible();
 await page.screenshot({path:'screenshots/friendly-startup-error.png'});
 await page.evaluate(()=>(window as any).__fail=false);
 await page.getByRole('button',{name:'Повторить подключение'}).click();
 await page.evaluate(()=>(window as any).__release());
 await expect(page.getByRole('heading',{name:'Мой проект',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>(window as any).__legacySeen)).toBe(false);
});
