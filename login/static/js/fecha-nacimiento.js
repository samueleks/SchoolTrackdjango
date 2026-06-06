(function (global) {
    'use strict';

    function extraerDigitosFecha(texto) {
        return (texto || '').replace(/\D/g, '').slice(0, 8);
    }

    function formatearDigitosFecha(digitos) {
        if (digitos.length <= 2) return digitos;
        if (digitos.length <= 4) return digitos.slice(0, 2) + '/' + digitos.slice(2);
        return digitos.slice(0, 2) + '/' + digitos.slice(2, 4) + '/' + digitos.slice(4);
    }

    function cursorDespuesDeDigitos(textoFormateado, cantidadDigitos) {
        if (cantidadDigitos <= 0) return 0;
        var vistos = 0;
        for (var i = 0; i < textoFormateado.length; i++) {
            if (/\d/.test(textoFormateado[i])) {
                vistos++;
                if (vistos === cantidadDigitos) return i + 1;
            }
        }
        return textoFormateado.length;
    }

    function fechaDesdeDigitosEsValida(digitos) {
        if (digitos.length !== 8) return false;
        var dia = parseInt(digitos.slice(0, 2), 10);
        var mes = parseInt(digitos.slice(2, 4), 10);
        var anio = parseInt(digitos.slice(4, 8), 10);
        var fecha = new Date(anio, mes - 1, dia);
        return (
            !isNaN(fecha.getTime()) &&
            fecha.getFullYear() === anio &&
            fecha.getMonth() === mes - 1 &&
            fecha.getDate() === dia
        );
    }

    function sincronizarFlatpickr(flatpickrInstance, digitos) {
        if (digitos.length === 8 && fechaDesdeDigitosEsValida(digitos)) {
            var iso = digitos.slice(4, 8) + '-' + digitos.slice(2, 4) + '-' + digitos.slice(0, 2);
            flatpickrInstance.setDate(iso, false);
            return;
        }
        if (digitos.length === 0) {
            flatpickrInstance.clear(false);
            return;
        }
        flatpickrInstance.input.value = '';
    }

    function actualizarMascara(altInput, flatpickrInstance, digitos, cursorDigitos) {
        var formateado = formatearDigitosFecha(digitos);
        altInput.value = formateado;
        sincronizarFlatpickr(flatpickrInstance, digitos);
        if (typeof cursorDigitos === 'number') {
            var nuevaPos = cursorDespuesDeDigitos(formateado, cursorDigitos);
            altInput.setSelectionRange(nuevaPos, nuevaPos);
        }
    }

    function aplicarMascaraTeclado(altInput, flatpickrInstance) {
        altInput.addEventListener('input', function () {
            var inicio = this.selectionStart;
            var fin = this.selectionEnd;
            var digitosAntes = inicio === fin
                ? extraerDigitosFecha(this.value.slice(0, inicio)).length
                : extraerDigitosFecha(this.value).length;
            var digitos = extraerDigitosFecha(this.value);
            actualizarMascara(this, flatpickrInstance, digitos, digitosAntes);
        });

        altInput.addEventListener('keydown', function (e) {
            if (e.key !== 'Backspace' || this.selectionStart !== this.selectionEnd) return;
            var pos = this.selectionStart;
            if (pos === 0) return;
            if (this.value[pos - 1] !== '/') return;

            e.preventDefault();
            var digitos = extraerDigitosFecha(this.value);
            var digitosAntes = extraerDigitosFecha(this.value.slice(0, pos)).length;
            if (digitosAntes === 0) return;

            var nuevosDigitos = digitos.slice(0, digitosAntes - 1) + digitos.slice(digitosAntes);
            actualizarMascara(this, flatpickrInstance, nuevosDigitos, digitosAntes - 1);
        });

        altInput.addEventListener('paste', function (e) {
            e.preventDefault();
            var pegado = (e.clipboardData || global.clipboardData).getData('text') || '';
            var inicio = this.selectionStart;
            var fin = this.selectionEnd;
            var digitosActuales = extraerDigitosFecha(this.value);
            var antes = extraerDigitosFecha(this.value.slice(0, inicio));
            var despues = extraerDigitosFecha(this.value.slice(fin));
            var digitosPegados = extraerDigitosFecha(pegado);
            var nuevosDigitos = (antes + digitosPegados + despues).slice(0, 8);
            var cursorDigitos = Math.min(antes.length + digitosPegados.length, 8);
            actualizarMascara(this, flatpickrInstance, nuevosDigitos, cursorDigitos);
        });
    }

    function inicializarFechaNacimiento(inputEl, opciones) {
        if (!inputEl || typeof flatpickr === 'undefined') return null;

        var opts = Object.assign({
            locale: 'es',
            dateFormat: 'Y-m-d',
            altFormat: 'd/m/Y',
            altInput: true,
            showMonths: 1,
            enableTime: false,
            disableMobile: true,
            allowInput: true
        }, opciones || {});

        var instance = flatpickr(inputEl, opts);

        if (instance.altInput) {
            instance.altInput.setAttribute('inputmode', 'numeric');
            instance.altInput.setAttribute('placeholder', 'DD/MM/YYYY');
            instance.altInput.setAttribute('autocomplete', 'bday');
            aplicarMascaraTeclado(instance.altInput, instance);
        }

        return instance;
    }

    global.SchoolTrackFechaNacimiento = {
        inicializar: inicializarFechaNacimiento,
        extraerDigitosFecha: extraerDigitosFecha,
        formatearDigitosFecha: formatearDigitosFecha
    };
})(typeof window !== 'undefined' ? window : this);
