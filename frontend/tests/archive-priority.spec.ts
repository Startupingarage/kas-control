import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {setupStudio} from './studio-fixture';

test('archive counters count copies and distinct blocked chats, not pauses or retries',async({page})=>{
 await page.clock.install();await setupStudio(page);
 await page.evaluate(()=>(window as any).__setArchive({voice_copied:3,remaining:14,blocked:36,note:'rate_limited',ordinary:{copied:2,errors:[{project_id:'core',source_id:'draft',detail:'Existing composer draft retained'},{project_id:'core',source_id:'draft',detail:'Existing composer draft retained'},{source_id:'rate',detail:'HTTP 429'}]},items:[{source_id:'voice',project_id:'core',state:'voice_busy'},{source_id:'rate',project_id:'core',state:'rate_limited'}]}));
 await page.clock.fastForward(13000);
 await expect(page.locator('.archive-metrics strong')).toHaveText(['5','14','1']);
 await expect(page.getByText('ChatGPT попросил сделать паузу',{exact:true})).toBeVisible();
 await page.screenshot({path:'screenshots/archive-priority.png',fullPage:true});
});

test('composer ignores visual link wraps but preserves real paragraph and br boundaries',async({page})=>{
 const source=readFileSync('../archive_desktop.py','utf8');
 const expression=source.match(/^EDITOR_TEXT="(.*)"$/m)![1];
 await page.setContent('<div contenteditable="true"><p><span>payload <a style="display:block">https://example.com</a> end</span></p></div>');
 expect(await page.evaluate(expr=>eval(expr)(document.querySelector('[contenteditable]')),expression)).toBe('payload https://example.com end');
 await page.setContent('<div contenteditable="true"><p>user</p><p>draft</p></div>');
 expect(await page.evaluate(expr=>eval(expr)(document.querySelector('[contenteditable]')),expression)).not.toBe('userdraft');
 await page.setContent('<div contenteditable="true"><p>user<br>draft</p></div>');
 expect(await page.evaluate(expr=>eval(expr)(document.querySelector('[contenteditable]')),expression)).toBe('user\ndraft');
});
