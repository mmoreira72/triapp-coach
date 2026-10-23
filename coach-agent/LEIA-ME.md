# Tri Squad: sincronização automática do WKO5 (Mac do treinador)

Todas as noites às 04:00 o Mac abre o WKO5, deixa-o sincronizar com o TrainingPeaks durante 15 minutos, fecha-o e copia as
bibliotecas dos atletas para a pasta do Google Drive que o Tri Squad lê. Às 05:00 o Tri Squad atualiza os treinos e o plano
de todos os atletas, mesmo os que não têm TrainingPeaks Premium.

Não muda nada no WKO5: a biblioteca fica onde está e nada é apagado no Drive.

## Instalar (uma vez, uns 5 minutos)

1. Confirma que tens o **Google Drive para computador** instalado e a pasta dos atletas sincronizada (a mesma para onde já
   envias os WKO).
2. Descarrega esta pasta (`coach-agent`) para o Mac, por exemplo para Transferências.
3. Abre a app **Terminal** (Aplicações → Utilitários → Terminal) e escreve, carregando em Enter no fim:

   ```
   bash ~/Downloads/coach-agent/install.sh
   ```

4. O instalador encontra o WKO5 e a biblioteca sozinho e abre uma janela para escolheres a **pasta do Google Drive dos
   atletas**. Escolhe-a e carrega em Escolher.
5. Se o macOS perguntar se o Terminal ou o "bash" pode aceder a pastas, ao Google Drive ou controlar o WKO5, carrega em
   **Permitir**.
6. No fim aparece "Pronto" e a última linha do registo diz quantos atletas foram copiados.

## Para funcionar

- O Mac tem de estar **ligado à corrente e com a sessão iniciada** à noite. Pode estar a dormir: o macOS corre a tarefa quando
  acordar. Para o acordar sempre às 03:55, em Definições do Sistema → Bateria → Opções (ou Poupança de energia → Agendar),
  escolhe "Ligar ou acordar" todos os dias às 03:55.
- Se o WKO5 **já estiver aberto** às 04:00, o agente não mexe nele e só copia.
- Se o WKO5 pedir para carregar em "Sync" e não sincronizar sozinho ao abrir, os dados ficam com a última sincronização que
  fizeste à mão; avisa o Marcelo.

## Ver se correu

Registo: `~/Library/Logs/triapp-wko-sync.log` (no Finder: Ir → Ir para a pasta… e cola o caminho). Cada noite tem uma linha
"copied N athlete libraries".

Correr já, sem esperar pela noite: `bash ~/Library/Application\ Support/TriSquad/triapp-wko-sync.sh`

## Desinstalar

`bash ~/Downloads/coach-agent/uninstall.sh`
