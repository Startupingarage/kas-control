import {test,expect} from '@playwright/test';
import {setupStudio,chooseProject} from './studio-fixture';
for(const size of [{width:1240,height:820},{width:980,height:640}])test('studio navigation and history '+size.width,async({page})=>{
 await page.setViewportSize(size);await setupStudio(page);
 await expect(page.getByRole('heading',{name:'Архив всех проектов',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Архивировать все проекты',exact:true})).toBeInViewport();
 await page.screenshot({animations:'disabled',path:`screenshots/studio-archive-${size.width}.png`});
 await page.getByRole('button',{name:'Разговор',exact:true}).click();await expect(page.getByRole('button',{name:'Разговор',exact:true})).toHaveAttribute('aria-current','page');
 await expect(page.getByRole('heading',{name:'Кухня и рецепты',exact:true})).toBeVisible();
 await expect(page.getByLabel('Фраза вызова')).toHaveValue('готовка');
 await page.screenshot({animations:'disabled',path:`screenshots/studio-conversation-${size.width}.png`});
 await page.getByRole('button',{name:'История',exact:true}).click();
 await expect(page.getByRole('heading',{name:'История проекта',exact:true})).toBeVisible();
 const last=page.getByRole('button',{name:/Разговор 34/});await last.scrollIntoViewIfNeeded();await expect(last).toBeVisible();await page.locator('.studio-main').evaluate(e=>e.scrollTop=0);
 await page.screenshot({animations:'disabled',path:`screenshots/studio-history-${size.width}.png`});
 await page.getByRole('button',{name:'Архив',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Архив всех проектов',exact:true})).toBeVisible();
});
test('project selection, command edits, new conversation and creation are isolated',async({page})=>{
 await setupStudio(page);await chooseProject(page,'Личные проекты');await page.getByRole('button',{name:'Разговор',exact:true}).click();
 await expect(page.getByLabel('Фраза вызова')).toHaveValue('голосовой чат');
 await page.getByLabel('Фраза вызова').fill('работа');await page.getByRole('button',{name:'Сохранить команду',exact:true}).click();
 await expect(page.getByRole('button',{name:'Сохранить команду',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'Открыть новый разговор',exact:true}).dblclick();
 await expect(page.locator('.session-state')).toContainText('Кас разговаривает');
 expect(await page.evaluate(()=>(window as any).__calls.filter((x:any)=>x.method==='open_project').length)).toBe(1);
 await page.getByRole('button',{name:'Закрыть чат',exact:true}).click();await expect(page.locator('.session-state')).toContainText('Сеанс закрыт');
 await page.getByRole('button',{name:'Убрать команду',exact:true}).click();await expect(page.getByRole('button',{name:'Открыть новый разговор',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'Выбрать проект'}).click();await page.getByLabel('Название нового проекта').fill('Новая мастерская');await page.getByRole('button',{name:'Создать проект',exact:true}).click();
 await expect(page.getByRole('dialog').getByRole('button',{name:'Новая мастерская',exact:true})).toBeVisible();await page.getByRole('button',{name:'Закрыть выбор проекта'}).click();
 await expect(page.getByRole('heading',{name:'Новая мастерская',exact:true})).toBeVisible();
});
test('project picker keyboard search and empty history',async({page})=>{
 await setupStudio(page);await page.keyboard.press('Control+k');await expect(page.getByRole('dialog')).toBeVisible();
 await page.getByLabel('Поиск проектов').fill('Новый');await page.screenshot({path:'screenshots/studio-project-picker.png'});
 await page.getByRole('dialog').getByRole('button',{name:'Новый проект',exact:true}).click();await page.getByRole('button',{name:'История',exact:true}).click();
 await expect(page.getByText('Пока нет сохраненных разговоров',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Открыть новый разговор',exact:true})).toBeDisabled();
});
test('history external link never opens voice and failures stay visible',async({page})=>{
 await setupStudio(page);await page.getByRole('button',{name:'История',exact:true}).click();await page.getByRole('button',{name:'План на неделю',exact:true}).click();
 expect(await page.evaluate(()=>(window as any).__calls.some((x:any)=>x.method==='open_project'))).toBe(false);
 await page.evaluate(()=>(window as any).__fail='list_chats');await page.getByRole('button',{name:'Обновить историю'}).click();await expect(page.getByRole('alert')).toContainText('Контрольная ошибка list_chats');
 await page.screenshot({animations:'disabled',path:'screenshots/studio-history-error.png'});
});
test('queue warnings and rate limit are visible and honest',async({page})=>{
 await setupStudio(page,{queue:true,rateLimit:true});await expect(page.getByText('ChatGPT попросил сделать паузу',{exact:true})).toBeVisible();await expect(page.getByText('Чат недоступен в ChatGPT',{exact:true})).not.toBeVisible();await page.getByText('Подробности последней проверки',{exact:false}).click();
 await expect(page.getByText('Чат недоступен в ChatGPT',{exact:true})).toBeVisible();
 await expect(page.getByText('Отправке мешает черновик',{exact:true})).toBeVisible();
 await page.screenshot({animations:'disabled',path:'screenshots/studio-rate-limit.png'});
});

test('unavailable conversation state explains disabled actions',async({page})=>{
 await setupStudio(page);await chooseProject(page,'Новый проект');await page.getByRole('button',{name:'Разговор',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Добавьте команду для запуска'})).toBeVisible();await expect(page.getByRole('button',{name:'Открыть новый разговор'})).toBeDisabled();
 await page.evaluate(()=>(window as any).__offline());await page.getByRole('button',{name:'Обновить состояние'}).click();
 await expect(page.getByRole('heading',{name:'Колонка пока недоступна'})).toBeVisible();await expect(page.locator('.session-state')).toContainText('Колонка недоступна');
});
test('creating a project from history never shows the previous project conversations',async({page})=>{
 await setupStudio(page);await page.getByRole('button',{name:'История',exact:true}).click();await expect(page.getByRole('button',{name:'План на неделю'})).toBeVisible();
 await page.getByRole('button',{name:'Выбрать проект'}).click();await page.getByLabel('Название нового проекта').fill('Новая история');await page.getByRole('button',{name:'Создать проект',exact:true}).click();await page.getByRole('button',{name:'Закрыть выбор проекта'}).click();
 await expect(page.getByRole('button',{name:'Выбрать проект'})).toContainText('Новая история');await expect(page.getByText('Пока нет сохраненных разговоров',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'План на неделю'})).toHaveCount(0);
});
