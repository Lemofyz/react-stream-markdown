import {test,expect} from '@playwright/test';
for(const reducedMotion of ['no-preference','reduce'] as const){
 test(`real browser lifecycle checks (${reducedMotion})`,async({page})=>{
  await page.emulateMedia({reducedMotion});
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/browser-checks.html');await page.getByRole('button',{name:'Run browser checks'}).click();
  await expect(page.getByRole('status')).toHaveText('PASS',{timeout:30000});
  const results=JSON.parse((await page.locator('#results').textContent())!);
  expect(results.passed).toBe(16);expect(results.reducedMotion).toBe(reducedMotion==='reduce');expect(errors).toEqual([]);
 });
}
test('demo compares identical streams and works on mobile',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/');await page.getByRole('button',{name:'Run stream',exact:true}).click();
 await expect(page.getByTestId('frame').getByRole('status')).toHaveText('complete');
 const states=await Promise.all(['frame','smoothed','event','sentence'].map(async id=>JSON.parse((await page.getByTestId(`${id}-json`).textContent())!)));
 expect(states[0].text).toBe(states[1].text);expect(states[0].text).toBe(states[2].text);expect(states[0].text).toBe(states[3].text);expect(states[0].marks.firstTextAt).toBe(states[1].marks.firstTextAt);
 expect(states[3].durations.firstTextToCommitMs).toBeGreaterThan(300);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('slow same-stream comparison reveals new letters before completion',async({page})=>{
 await page.goto('/?playback=slow');await page.getByRole('button',{name:'Run stream',exact:true}).click();
 const original=page.getByRole('region',{name:'Original reply',exact:true});const smoothed=page.getByRole('region',{name:'Letter reveal reply',exact:true});
 await expect(original).toHaveText('You can start reading');await expect(smoothed).toHaveText('You can start reading');
 expect(await smoothed.locator('span').count()).toBeGreaterThan(1);
 await expect(smoothed).toHaveText('You can start reading before the reply');
 await page.getByRole('button',{name:'Stop',exact:true}).click();
 await expect(page.getByTestId('smoothed').getByRole('status')).toHaveText('interrupted');
 expect(await smoothed.locator('span').evaluateAll(els=>els.every(el=>getComputedStyle(el).opacity==='1'&&el.getAnimations().length===0))).toBe(true);
});
