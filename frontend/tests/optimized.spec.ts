import {test,expect} from '@playwright/test';
import {setupStudio} from './studio-fixture';

test('archive explains the persisted next run and tray requirement',async({page})=>{
 await setupStudio(page,{view:'archive',nextRun:1794549600});
 await expect(page.locator('.archive-next-run')).toContainText('Следующая проверка:');
 await expect(page.locator('.archive-next-run')).toContainText('Пока Kas Control работает в трее.');
 await page.screenshot({path:'screenshots/optimized-archive-schedule.png'});
});

test('slow cloud catalog does not block local conversation controls',async({page})=>{
 await setupStudio(page,{view:'conversation',deferredCatalog:true});
 await expect(page.getByText('Обновляем список…',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Добавить проект',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'Открыть новый разговор',exact:true}).click();
 await expect(page.locator('.session-state')).toContainText('Кас разговаривает');
 await page.evaluate(()=>(window as any).__finishCatalog(true));
 await expect(page.getByText('Облако недоступно',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Добавить проект',exact:true})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Закрыть чат',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'Восстановить разговор',exact:true}).click();
 expect(await page.evaluate(()=>(window as any).__calls.filter((c:any)=>c.method==='reconnect_project').length)).toBe(1);
 await expect(page.getByRole('button',{name:'Закрыть чат',exact:true})).toBeEnabled();
 await page.screenshot({path:'screenshots/optimized-cloud-unavailable.png'});
});

test('diagnostics are generated only by explicit click and show local result',async({page})=>{
 await setupStudio(page,{view:'conversation'});
 expect(await page.evaluate(()=>(window as any).__calls.some((c:any)=>c.method==='diagnostics'))).toBe(false);
 await page.getByText('Диагностика и версия',{exact:true}).click();
 await page.getByRole('button',{name:'Собрать диагностику',exact:true}).click();
 await expect(page.getByText('C:/Mock/KasControl/Diagnostics/report.zip',{exact:true})).toBeVisible();
});
