import {expect, test} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import {setupStudio} from '../tests/studio-fixture';

test('capture the project guide with fictional data', async ({page, baseURL}) => {
  const output = path.resolve('..', 'docs', 'images');
  await mkdir(output, {recursive: true});
  await page.setViewportSize({width: 1280, height: 900});

  const localOrigin = new URL(baseURL!).origin;
  const externalRequests: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const origin = new URL(route.request().url()).origin;
    if (origin === localOrigin) return route.continue();
    externalRequests.push(origin);
    return route.abort();
  });

  const checkedAt = Date.parse('2026-11-24T21:00:00+03:00') / 1000;
  await setupStudio(page, {
    view: 'conversation',
    nextRun: checkedAt + 12 * 3600,
    example: {
      checkedAt,
      projectNames: {core: 'Дом и порядок', long: 'Книги и кино', empty: 'Ремонт квартиры'},
    },
  });
  await expect(page.getByRole('heading', {name: 'Кухня и рецепты'})).toBeVisible();
  await expect(page.getByLabel('Фраза вызова')).toHaveValue('готовка');
  await expect(page.getByRole('button', {name: 'Открыть новый разговор'}).first()).toBeEnabled();
  await page.screenshot({path: path.join(output, 'project-command.png'), animations: 'disabled'});

  await page.getByRole('button', {name: 'Архив', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Архив всех проектов'})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Архивировать все проекты'})).toBeEnabled();
  await page.mouse.move(0, 0);
  await page.screenshot({path: path.join(output, 'archive.png'), animations: 'disabled'});

  expect(externalRequests).toEqual([]);
  expect(errors).toEqual([]);
});
