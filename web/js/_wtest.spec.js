const { test, expect } = require('@playwright/test');
const BASE = 'http://127.0.0.1:8420';
test('gdpr generic-terms chip manager', async ({ page }) => {
  test.setTimeout(60000);
  const errs=[];
  page.on('console', m=>{ if(m.type()==='error'){const t=m.text(); if(!/Failed to load resource|net::ERR|favicon|status of/i.test(t)) errs.push(t);}});
  page.on('pageerror', e=>errs.push('PAGEERR: '+e.message));
  await page.goto(BASE, { waitUntil:'load' });
  const uf=page.locator('#auth-username');
  if(await uf.isVisible().catch(()=>false)){ await uf.fill('admin'); await page.locator('#auth-password').fill('admin'); await page.getByRole('button',{name:'Anmelden'}).click(); }
  await expect(page.locator('#welcome-view')).toBeVisible({timeout:15000});
  await page.waitForFunction(()=>typeof state==='object'&&state&&state.modelsConfigReady===true,null,{timeout:30000});
  // Open general settings + GDPR tab programmatically (avoid brittle clicks).
  await page.evaluate(async ()=>{ if(typeof openGeneralSettings==='function') openGeneralSettings(); });
  await page.waitForTimeout(400);
  // Switch to the GDPR tab
  await page.evaluate(async ()=>{
    const C=document.getElementById('general-tab-content');
    if(typeof _genTab_gdpr==='function' && C) await _genTab_gdpr(C);
  });
  await page.waitForTimeout(1200);
  const mgr = page.locator('#gdpr-generic-terms-mgr');
  await expect(mgr).toBeVisible({timeout:8000});
  const chipCount = await page.evaluate(()=> (state.gdprGenericTerms||[]).length);
  console.log('LOADED TERMS:', chipCount);
  const search = page.locator('#gdpr-term-search');
  await expect(search).toBeVisible();
  // Filter to 'govern'
  await page.evaluate(()=>gdprTermFilter('govern'));
  await page.waitForTimeout(200);
  const shownAfterFilter = await page.locator('#gdpr-generic-terms-mgr span button[onclick^="gdprTermRemove"]').count();
  console.log('CHIPS after filter govern:', shownAfterFilter);
  // Add a new term
  await page.evaluate(()=>gdprTermFilter(''));
  await page.evaluate(()=>{ document.getElementById('gdpr-term-add').value='vorstand'; gdprTermAdd(); });
  const hasVorstand = await page.evaluate(()=> state.gdprGenericTerms.includes('vorstand'));
  console.log('added vorstand:', hasVorstand);
  // Remove a term
  await page.evaluate(()=>gdprTermRemove('governance'));
  const hasGov = await page.evaluate(()=> state.gdprGenericTerms.includes('governance'));
  console.log('governance removed:', !hasGov);
  console.log('CONSOLE ERRORS:', JSON.stringify(errs));
  expect(chipCount).toBeGreaterThan(100);
  expect(hasVorstand).toBe(true);
  expect(hasGov).toBe(false);
  expect(errs).toEqual([]);
});
