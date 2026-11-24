import {test,expect} from '@playwright/test';
import {setupStudio,chooseProject} from './studio-fixture';

test('default screen shows conversation and sidebar command indicators',async({page})=>{
 await setupStudio(page,{view:'conversation'});
 await expect(page.getByRole('heading',{name:'Кухня и рецепты',exact:true})).toBeVisible();
 const sidebar=page.getByRole('navigation',{name:'Список проектов'});
 await expect(sidebar.getByRole('img',{name:'Команда назначена'})).toHaveCount(2);
 await page.getByLabel('Найти проект в списке').fill('учеба');
 await expect(sidebar.getByRole('button')).toHaveCount(1);
 await expect(sidebar.getByRole('button',{name:'Учеба и языки'})).toBeVisible();
 expect(await page.evaluate(()=>(window as any).__calls.some((x:any)=>x.method==='open_project'))).toBe(false);
});
test('studio preserves project selection across views and handles long names',async({page})=>{
 await page.setViewportSize({width:980,height:640});await setupStudio(page);
 await chooseProject(page,'Длинное название проекта для проверки переноса текста');await page.getByRole('button',{name:'Разговор',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Длинное название проекта для проверки переноса текста'})).toBeVisible();
 await page.screenshot({path:'screenshots/studio-long-project-980.png'});
 await page.getByRole('button',{name:'Архив',exact:true}).click();await expect(page.locator('.sidebar-project-list button[aria-current=page]')).toContainText('Длинное название');
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);expect(overflow).toBe(false);
});
