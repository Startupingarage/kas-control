import {test,expect} from '@playwright/test';
import {setupStudio,chooseProject} from './studio-fixture';
test('ordinary copy switch is project scoped and mutations refresh locally',async({page})=>{
 await setupStudio(page);await page.getByText('Обычные чаты — какие проекты сохранять',{exact:true}).click();const toggle=page.getByRole('switch',{name:'Копировать обычные чаты: Кухня и рецепты'});
 await expect(toggle).toHaveAttribute('aria-checked','false');await toggle.click();await expect(toggle).toHaveAttribute('aria-checked','true');
 await expect(page.getByRole('switch',{name:'Копировать обычные чаты: Новый проект'})).toHaveAttribute('aria-checked','false');await expect(toggle).toHaveAttribute('aria-checked','true');
 await page.getByRole('button',{name:'Вручную',exact:true}).click();await expect(page.getByRole('button',{name:'Вручную',exact:true})).toHaveAttribute('aria-pressed','true');
 await page.getByRole('button',{name:'Автоматически два раза в сутки',exact:true}).click();await expect(page.getByRole('button',{name:'Автоматически два раза в сутки',exact:true})).toHaveAttribute('aria-pressed','true');
 expect(await page.evaluate(()=>(window as any).__calls.filter((x:any)=>x.method==='bootstrap').length)).toBe(1);
 await page.getByText('Как сохраняются разговоры',{exact:true}).click();await expect(page.getByText('Оригиналы обычных чатов не удаляются.',{exact:false})).toBeVisible();
});
test('archive start stop and external hourly start update without cloud refresh',async({page})=>{
 await page.clock.install();await setupStudio(page);
 await page.getByRole('button',{name:'Архивировать все проекты',exact:true}).click();await expect(page.getByRole('button',{name:'Остановить архивирование'})).toBeVisible();
 expect(await page.evaluate(()=>(window as any).__calls.find((x:any)=>x.method==='start_archive').args)).toEqual([]);
 await page.getByRole('button',{name:'Остановить архивирование'}).click();await expect(page.getByRole('button',{name:'Архивировать все проекты',exact:true})).toBeVisible();
 await page.evaluate(()=>(window as any).__startExternal());await page.clock.fastForward(12200);await expect(page.getByRole('button',{name:'Остановить архивирование'})).toBeVisible();
 await page.evaluate(()=>(window as any).__finish());await page.clock.fastForward(2300);await expect(page.getByRole('button',{name:'Архивировать все проекты',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>(window as any).__calls.filter((x:any)=>x.method==='bootstrap').length)).toBe(1);
});
test('running archive shows source progress and neutral timestamp',async({page})=>{
 await setupStudio(page,{running:true,note:'running'});await expect(page.getByText('Подбор режима приготовления',{exact:true})).toBeVisible();
 await expect(page.getByText('Обновлено ',{exact:false})).toBeVisible();await expect(page.getByText('Сохраняем сейчас · все проекты',{exact:true})).toBeVisible();
 await page.screenshot({path:'screenshots/studio-progress.png'});
});

test('shared ChatGPT memory is not treated as an archival error',async({page})=>{
 await setupStudio(page,{memoryScope:'global'});
 await expect(page.getByText('Память выбранного проекта общая',{exact:true})).toHaveCount(0);
 await chooseProject(page,'Личные проекты');
 await expect(page.getByText('Память выбранного проекта общая',{exact:true})).toHaveCount(0);
 await expect(page.getByText('Память выбранного проекта изолирована',{exact:true})).toHaveCount(0);
});
