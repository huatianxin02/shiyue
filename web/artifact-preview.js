/* Shared, local-only artifact preview for the console and the workbench. */
(() => {
  'use strict';
  let dialog;
  let objectUrl = null;
  let active = 0;
  let handledCloseEvents = 0;

  function cleanup() {
    active++;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    dialog?.querySelector('[data-preview-content]')?.replaceChildren();
  }

  function shell() {
    if (dialog) return dialog;
    dialog = document.createElement('dialog');
    dialog.className = 'artifact-preview-dialog';
    dialog.style.width = 'min(1100px,94vw)';
    const heading = document.createElement('div');
    heading.className = 'dialog-head';
    const title = document.createElement('h2');
    title.dataset.previewTitle = '';
    const button = document.createElement('button');
    button.textContent = '关闭预览';
    button.addEventListener('click', close);
    heading.append(title, button);
    const help = document.createElement('p');
    help.className = 'fine';
    help.dataset.previewHelp = '';
    const content = document.createElement('div');
    content.dataset.previewContent = '';
    const download = document.createElement('a');
    download.textContent = '下载校验版本';
    download.dataset.previewDownload = '';
    download.style.textDecoration = 'underline';
    dialog.append(heading, help, content, download);
    dialog.addEventListener('cancel', event => {event.preventDefault(); close();});
    dialog.addEventListener('close', () => {
      if (handledCloseEvents) handledCloseEvents--;
      else cleanup();
    });
    document.body.append(dialog);
    return dialog;
  }

  function staticHtml(text) {
    const template = document.createElement('template');
    template.innerHTML = text;
    template.content.querySelectorAll('script,iframe,frame,frameset,object,embed,link,base,meta,form,template,noscript').forEach(node => node.remove());
    for (const element of template.content.querySelectorAll('*')) {
      for (const attribute of [...element.attributes]) {
        const name = attribute.name.toLowerCase();
        const value = attribute.value.trim();
        if (name.startsWith('on') || ['srcdoc', 'srcset', 'action', 'formaction', 'ping', 'target', 'download'].includes(name)) {
          element.removeAttribute(attribute.name);
        } else if (['href', 'xlink:href'].includes(name) && !value.startsWith('#')) {
          element.removeAttribute(attribute.name);
        } else if (['src', 'poster', 'background'].includes(name) && !/^data:image\//i.test(value)) {
          element.removeAttribute(attribute.name);
        }
      }
    }
    return '<!doctype html><html><head><meta charset="utf-8">'
      + '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; font-src data:; base-uri \'none\'; form-action \'none\'">'
      + '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>' + template.innerHTML + '</body></html>';
  }

  function kind(name, mime = '') {
    const extension = name.split('.').pop().toLowerCase();
    if (['html', 'htm'].includes(extension) || mime === 'text/html') return 'html';
    if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'bmp', 'svg'].includes(extension)) return 'image';
    if (extension === 'pdf' || mime === 'application/pdf') return 'pdf';
    if (['md', 'txt', 'json', 'csv', 'tsv', 'py', 'js', 'mjs', 'cjs', 'css', 'xml', 'log', 'yaml', 'yml'].includes(extension) || mime.startsWith('text/')) return 'text';
    return 'file';
  }

  async function open({name, mime = '', blob, sha256}) {
    if (!(blob instanceof Blob) || !name) throw Error('需要有效的校验文件');
    close();
    const request = ++active;
    const element = shell();
    const bytes = await blob.arrayBuffer();
    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
    if (request !== active) return;
    if (sha256 && digest !== sha256) throw Error('下载内容与登记哈希不同，未打开预览');
    const format = kind(name, mime);
    if (format === 'pdf' && new TextDecoder().decode(bytes.slice(0, 5)) !== '%PDF-') {
      throw Error('所选文件没有有效的 PDF 文件头，请下载后核对');
    }
    const media = {png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp',avif:'image/avif',bmp:'image/bmp',svg:'image/svg+xml'};
    const extension = name.split('.').pop().toLowerCase();
    const type = format === 'pdf' ? 'application/pdf' : format === 'image' ? media[extension] : blob.type;
    objectUrl = URL.createObjectURL(new Blob([bytes], {type}));
    element.querySelector('[data-preview-title]').textContent = name;
    const download = element.querySelector('[data-preview-download]');
    download.href = objectUrl;
    download.download = name;
    const content = element.querySelector('[data-preview-content]');
    const help = element.querySelector('[data-preview-help]');
    if (format === 'html' && bytes.byteLength <= 8 * 1024 * 1024) {
      const frame = document.createElement('iframe');
      frame.setAttribute('sandbox', '');
      frame.referrerPolicy = 'no-referrer';
      frame.title = name + ' 静态预览';
      frame.srcdoc = staticHtml(new TextDecoder().decode(bytes));
      frame.style.cssText = 'width:100%;height:65vh;border:1px solid #bdccc3;border-radius:8px;background:white';
      content.append(frame);
      help.textContent = '静态 HTML 预览 · 资源内嵌内容可显示 · SHA-256 已核对';
    } else if (format === 'image') {
      const image = document.createElement('img');
      image.alt = name;
      image.src = objectUrl;
      image.style.cssText = 'display:block;max-width:100%;max-height:65vh;margin:auto;object-fit:contain';
      content.append(image);
      help.textContent = '图片预览 · SHA-256 已核对';
    } else if (format === 'pdf') {
      const frame = document.createElement('iframe');
      frame.referrerPolicy = 'no-referrer';
      frame.title = name + ' PDF 预览';
      frame.src = objectUrl;
      frame.style.cssText = 'width:100%;height:65vh;border:1px solid #bdccc3;border-radius:8px';
      content.append(frame);
      help.textContent = 'PDF 使用浏览器阅读器显示。也可下载后用本机阅读器打开。';
    } else if (format === 'text' && bytes.byteLength <= 8 * 1024 * 1024) {
      const pre = document.createElement('pre');
      pre.textContent = new TextDecoder().decode(bytes);
      pre.style.maxHeight = '65vh';
      content.append(pre);
      help.textContent = '文本预览 · SHA-256 已核对';
    } else {
      const description = document.createElement('p');
      description.textContent = '该文件可下载后使用本机应用查看。';
      content.append(description);
      help.textContent = 'SHA-256 已核对';
    }
    if (!element.open) element.showModal();
    return {format, sha256: digest};
  }

  function close() {
    cleanup();
    if (dialog?.open) {
      handledCloseEvents++;
      dialog.close();
    }
  }

  window.ArtifactPreview = {open, close};
})();
