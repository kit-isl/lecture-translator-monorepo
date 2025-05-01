const puppeteer = require('puppeteer');
const fs = require('fs');

async function processData(url) {
    const browser = await puppeteer.launch({headless: true, args: ['--no-sandbox'] });  // Launch headless browser
    const page = await browser.newPage();


    await page.goto(url, { waitUntil: 'networkidle2' });  // Navigate to the URL

    page.on('console', msg => {
        console.log(`PAGE LOG: ${msg.text()}`); // Print log messages from the page
    });

    // Call a JS function from the page
    await page.evaluate(() => {
        if (typeof window.updateAllWindows === 'function') {
            console.log("Process function found");
            window.updateAllWindows();
        }else {
            console.log("Function not found");
        }
        console.log("All windows upated");
    });

    let processedHTML = await page.content();  // Extract final HTML


    await browser.close();
    return processedHTML;
}

// Read JSON data from command line
const url = process.argv[2];
const ID = process.argv[3];

processData(url).then(processedHTML => {
    fs.writeFileSync('/tmp/processed.'+ID+'.html', processedHTML);  // Save to file
    console.log("Processed HTML saved.");
});
