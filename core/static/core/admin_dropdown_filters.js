/*
 * Cambio #11: filtros como desplegable de selección múltiple en los
 * encabezados de columna de la tabla de Facturas (Sucursal, Municipio,
 * Canal, Ruta, Carga). No usa jQuery para no depender de qué versión trae
 * el admin de Django.
 *
 * Cómo funciona:
 * - Los valores posibles de cada columna llegan como JSON embebido en la
 *   página (ver <script id="filtro-columnas-data">, generado desde
 *   FacturaAdmin.changelist_view en admin.py).
 * - Los valores seleccionados se guardan en la URL como "f_<campo>",
 *   con varios valores separados por coma (por ejemplo
 *   "f_municipio=Managua,León"). FacturaAdmin.get_queryset lee esos mismos
 *   parámetros para filtrar.
 * - Al aplicar o limpiar un filtro, se recarga la página con la URL
 *   actualizada (mismo patrón que ya usa el admin de Django para todo lo
 *   demás: orden, búsqueda, paginación).
 */
(function () {
    "use strict";

    function alListo(fn) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", fn);
        } else {
            fn();
        }
    }

    alListo(function () {
        var datosEl = document.getElementById("filtro-columnas-data");
        var tabla = document.getElementById("result_list");
        if (!datosEl || !tabla) {
            return;
        }

        var columnas;
        try {
            columnas = JSON.parse(datosEl.textContent);
        } catch (err) {
            return;
        }

        var params = new URLSearchParams(window.location.search);

        function valoresSeleccionados(campo) {
            var crudo = params.get("f_" + campo);
            if (!crudo) {
                return [];
            }
            return crudo.split(",").filter(function (v) {
                return v !== "";
            });
        }

        function etiquetaPara(campo, valor) {
            var opciones = columnas[campo] && columnas[campo].options ? columnas[campo].options : [];
            for (var i = 0; i < opciones.length; i++) {
                if (opciones[i].value === valor) {
                    return opciones[i].label;
                }
            }
            return valor;
        }

        function navegarCon(nuevosValoresPorCampo) {
            var nuevosParams = new URLSearchParams(window.location.search);
            Object.keys(nuevosValoresPorCampo).forEach(function (campo) {
                var valores = nuevosValoresPorCampo[campo];
                var clave = "f_" + campo;
                if (valores && valores.length > 0) {
                    nuevosParams.set(clave, valores.join(","));
                } else {
                    nuevosParams.delete(clave);
                }
            });
            // Al cambiar un filtro, volvemos a la página 1 del listado.
            nuevosParams.delete("p");
            window.location.search = nuevosParams.toString();
        }

        function cerrarTodosLosDropdowns(exceptoEste) {
            document.querySelectorAll(".fdc-dropdown.fdc-abierto").forEach(function (dd) {
                if (dd !== exceptoEste) {
                    dd.classList.remove("fdc-abierto");
                }
            });
        }

        function construirDropdown(campo, opciones, seleccionActual) {
            var dropdown = document.createElement("div");
            dropdown.className = "fdc-dropdown";
            dropdown.dataset.campo = campo;

            var buscar = document.createElement("div");
            buscar.className = "fdc-buscar";
            var inputBuscar = document.createElement("input");
            inputBuscar.type = "text";
            inputBuscar.placeholder = "Buscar...";
            buscar.appendChild(inputBuscar);
            dropdown.appendChild(buscar);

            var contenedorOpciones = document.createElement("div");
            contenedorOpciones.className = "fdc-opciones";

            if (opciones.length === 0) {
                var vacio = document.createElement("p");
                vacio.className = "fdc-vacio";
                vacio.textContent = "Sin opciones disponibles";
                contenedorOpciones.appendChild(vacio);
            }

            opciones.forEach(function (opcion) {
                var label = document.createElement("label");
                var checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.value = opcion.value;
                checkbox.checked = seleccionActual.indexOf(opcion.value) !== -1;
                label.appendChild(checkbox);
                label.appendChild(document.createTextNode(opcion.label));
                label.dataset.texto = opcion.label.toLowerCase();
                contenedorOpciones.appendChild(label);
            });
            dropdown.appendChild(contenedorOpciones);

            inputBuscar.addEventListener("input", function () {
                var texto = inputBuscar.value.toLowerCase();
                contenedorOpciones.querySelectorAll("label").forEach(function (label) {
                    var coincide = !label.dataset.texto || label.dataset.texto.indexOf(texto) !== -1;
                    label.style.display = coincide ? "" : "none";
                });
            });

            var acciones = document.createElement("div");
            acciones.className = "fdc-acciones";

            var btnLimpiar = document.createElement("button");
            btnLimpiar.type = "button";
            btnLimpiar.className = "fdc-limpiar";
            btnLimpiar.textContent = "Limpiar";
            btnLimpiar.addEventListener("click", function () {
                var cambios = {};
                cambios[campo] = [];
                navegarCon(cambios);
            });

            var btnAplicar = document.createElement("button");
            btnAplicar.type = "button";
            btnAplicar.className = "fdc-aplicar";
            btnAplicar.textContent = "Aplicar";
            btnAplicar.addEventListener("click", function () {
                var marcados = Array.prototype.slice
                    .call(contenedorOpciones.querySelectorAll("input[type=checkbox]:checked"))
                    .map(function (c) {
                        return c.value;
                    });
                var cambios = {};
                cambios[campo] = marcados;
                navegarCon(cambios);
            });

            acciones.appendChild(btnLimpiar);
            acciones.appendChild(btnAplicar);
            dropdown.appendChild(acciones);

            return dropdown;
        }

        Object.keys(columnas).forEach(function (campo) {
            var th = tabla.querySelector("thead th.column-" + campo);
            if (!th) {
                return;
            }
            th.classList.add("fdc-con-filtro");

            var opciones = columnas[campo].options || [];
            var seleccionActual = valoresSeleccionados(campo);

            var boton = document.createElement("button");
            boton.type = "button";
            boton.className = "fdc-boton";
            if (seleccionActual.length > 0) {
                boton.classList.add("fdc-activo");
            }
            boton.setAttribute("aria-label", "Filtrar por " + (columnas[campo].label || campo));
            boton.innerHTML = "&#9660;";

            var dropdown = construirDropdown(campo, opciones, seleccionActual);

            boton.addEventListener("click", function (evento) {
                evento.stopPropagation();
                var yaAbierto = dropdown.classList.contains("fdc-abierto");
                cerrarTodosLosDropdowns();
                if (!yaAbierto) {
                    dropdown.classList.add("fdc-abierto");
                }
            });

            var contenedorTexto = th.querySelector(".text") || th;
            contenedorTexto.appendChild(boton);
            th.appendChild(dropdown);
        });

        document.addEventListener("click", function (evento) {
            if (!evento.target.closest(".fdc-dropdown") && !evento.target.closest(".fdc-boton")) {
                cerrarTodosLosDropdowns();
            }
        });

        // Barra de "chips" con los filtros activos, arriba de la tabla.
        var chipsBar = document.createElement("div");
        chipsBar.id = "fdc-chips-bar";

        var camposConFiltro = Object.keys(columnas);
        var hayAlgunFiltro = camposConFiltro.some(function (campo) {
            return valoresSeleccionados(campo).length > 0;
        });

        if (hayAlgunFiltro) {
            camposConFiltro.forEach(function (campo) {
                valoresSeleccionados(campo).forEach(function (valor) {
                    var chip = document.createElement("span");
                    chip.className = "fdc-chip";
                    chip.textContent = (columnas[campo].label || campo) + ": " + etiquetaPara(campo, valor);

                    var btnQuitar = document.createElement("button");
                    btnQuitar.type = "button";
                    btnQuitar.textContent = "×";
                    btnQuitar.addEventListener("click", (function (campoCerrado, valorCerrado) {
                        return function () {
                            var restantes = valoresSeleccionados(campoCerrado).filter(function (v) {
                                return v !== valorCerrado;
                            });
                            var cambios = {};
                            cambios[campoCerrado] = restantes;
                            navegarCon(cambios);
                        };
                    })(campo, valor));

                    chip.appendChild(btnQuitar);
                    chipsBar.appendChild(chip);
                });
            });

            var btnQuitarTodos = document.createElement("button");
            btnQuitarTodos.type = "button";
            btnQuitarTodos.className = "fdc-quitar-todos";
            btnQuitarTodos.textContent = "Quitar todos";
            btnQuitarTodos.addEventListener("click", function () {
                var cambios = {};
                camposConFiltro.forEach(function (campo) {
                    cambios[campo] = [];
                });
                navegarCon(cambios);
            });
            chipsBar.appendChild(btnQuitarTodos);

            var resultados = tabla.closest(".results") || tabla.parentNode;
            resultados.parentNode.insertBefore(chipsBar, resultados);
        }
    });
})();
