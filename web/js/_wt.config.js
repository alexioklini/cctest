const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({ testDir: __dirname, testMatch:'_wtest.spec.js', workers:1, retries:0, timeout:60000, reporter:[['line']], use:{headless:true,viewport:{width:1440,height:900}} });
