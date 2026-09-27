import {test,expect} from '@playwright/test';
for(const reducedMotion of ['no-preference','reduce'] as const){
 test(`real browser lifecycle checks (${reducedMotion})`,async({page})=>{
  await page.emulateMedia({reducedMotion});
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/browser-checks.html');await page.getByRole('button',{name:'Run browser checks'}).click();
  await expect(page.getByRole('status')).toHaveText('PASS',{timeout:20000});
  const results=JSON.parse((await page.locator('#results').textContent())!);
  expect(results.passed).toBe(8);expect(results.reducedMotion).toBe(reducedMotion==='reduce');expect(errors).toEqual([]);
 });
}
test('demo compares identical streams and works on mobile',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/');await page.getByRole('button',{name:'Run stream',exact:true}).click();
 await expect(page.getByTestId('frame').getByRole('status')).toHaveText('complete');
 const states=await Promise.all(['frame','event','sentence'].map(async id=>JSON.parse((await page.getByTestId(`${id}-json`).textContent())!)));
 expect(states[0].text).toBe(states[1].text);expect(states[0].text).toBe(states[2].text);
 expect(states[2].durations.firstTextToCommitMs).toBeGreaterThan(300);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
