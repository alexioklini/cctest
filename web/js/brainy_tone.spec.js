// Manual tone check (not a hard assertion gate): ask Brainy the exact
// "how often did I ask about the weather" question and print the reply so we
// can eyeball that it stays correct AND adds a dry closing quip. Run on demand.
const { test } = require('@playwright/test');
const { login, askBrainy } = require('./brainy_helpers');

test('Brainy tone — weather-frequency question', async ({ page }) => {
  await login(page);
  const r = await askBrainy(page, 'Wie oft habe ich in den Chats nach dem Wetter gefragt?',
    { viewContext: { view: 'chats', label: 'Chat-Liste' } });
  console.log('\n=== TOOLS:', r.toolCalls, '| ERROR:', r.error, '===');
  console.log('=== REPLY ===\n' + (r.reply || '(leer)') + '\n=== END ===\n');
});
