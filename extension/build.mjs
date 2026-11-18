import {build} from 'esbuild';
import {mkdir,copyFile} from 'node:fs/promises';
await mkdir('dist',{recursive:true});
await build({entryPoints:['src/background.ts','src/content.ts','src/panel.ts'],bundle:true,outdir:'dist',format:'iife',target:'chrome120'});
for(const name of ['manifest.json','panel.html','panel.css'])await copyFile(name,'dist/'+name);
