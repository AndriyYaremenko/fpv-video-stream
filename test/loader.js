// test/loader.js — preloaded via --import to register browser-path rewriting hooks.
import { register } from 'node:module';

register('./loader-hooks.js', import.meta.url);
