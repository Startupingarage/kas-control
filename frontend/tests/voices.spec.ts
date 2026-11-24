import {test,expect} from '@playwright/test';

for(const projectMode of [true,false]){
 test(`voice selection in ${projectMode?'project':'legacy'} mode`,async({page})=>{
  await page.addInitScript(({projectMode})=>{
   const state={project_sessions:projectMode,project_status:{prepared:{core:8},hot_preparation:true,archive:{running:false,available:false}},projects:[{id:'core',name:'Core Drone OS'}],current:{project_id:'core',phase:'active',voice:'orbit'},router_ready:true,native_commands:[],routes:[]};
   (window as any).__calls=[];
   (window as any).pywebview={api:{bootstrap:async()=>({ok:true,data:structuredClone(state)}),list_chats:async()=>({ok:true,data:[]}),set_voice:async(voice:string)=>{(window as any).__calls.push(['set_voice',voice]);state.current.voice=voice;return {ok:true,data:{voice,next_session:true}};}}};
  },{projectMode});
  await page.goto('/');
  await page.getByRole('button',{name:'Голос: Spruce',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Голос Каса'});
  await expect(dialog.getByText('Новый голос включится при следующем открытии разговора. Текущий сеанс продолжится прежним голосом.')).toBeVisible();
  await expect(dialog.getByRole('button',{name:'Сохранить голос'})).toBeDisabled();
  await dialog.getByRole('combobox',{name:'Голос'}).click();
  await expect(page.getByRole('option')).toHaveCount(9);
  await page.getByRole('option',{name:'Ember',exact:true}).click();
  await page.screenshot({path:`screenshots/voice-${projectMode?'project':'legacy'}.png`});
  expect(await page.evaluate(()=>(window as any).__calls)).toEqual([]);
  await dialog.getByRole('button',{name:'Сохранить голос'}).click();
  await expect(dialog.getByRole('status')).toHaveText('Голос сохранен: Ember. Включится при следующем открытии.');
  await expect(page.getByRole('button',{name:'Голос: Ember',exact:true,includeHidden:true})).toBeVisible();
  await dialog.getByRole('button',{name:'Закрыть выбор голоса'}).click();
  await page.getByRole('button',{name:'Голос: Ember',exact:true}).click();
  await expect(dialog.getByRole('combobox',{name:'Голос'})).toHaveText('Ember');
  await page.keyboard.press('Escape');
  expect(await page.evaluate(()=>(window as any).__calls)).toEqual([['set_voice','ember']]);
 });
}

test('voice failure leaves dialog open and never reports success',async({page})=>{
 await page.addInitScript(()=>{
  (window as any).pywebview={api:{bootstrap:async()=>({ok:true,data:{project_sessions:true,projects:[{id:'core',name:'Core Drone OS'}],current:{project_id:'core',phase:'idle',voice:'orbit'},router_ready:true,routes:[],native_commands:[]}}),set_voice:async()=>({ok:false,error:'Колонка недоступна.'})}};
 });
 await page.goto('/');await page.getByRole('button',{name:'Голос: Spruce',exact:true}).click();
 await page.getByRole('combobox',{name:'Голос'}).click();await page.getByRole('option',{name:'Vale',exact:true}).click();
 await page.getByRole('button',{name:'Сохранить голос'}).click();
 await expect(page.getByRole('dialog').getByRole('alert')).toHaveText('Колонка недоступна.');
 await expect(page.getByRole('button',{name:'Голос: Spruce',exact:true,includeHidden:true})).toBeVisible();
});
