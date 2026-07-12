import win32print
from django.utils import timezone
import time
import logging

logger = logging.getLogger(__name__)


def print_ticket(ticket):
    """Print a ticket to the EPSON thermal printer using ESC/POS raw commands."""
    printer_name = "EPSON TM-T88V Receipt"
    hPrinter = None
    try:
        hPrinter = win32print.OpenPrinter(printer_name)
        win32print.StartDocPrinter(hPrinter, 1, ("QMS Ticket", None, "RAW"))
        win32print.StartPagePrinter(hPrinter)

        # Get current datetime
        now = timezone.localtime(ticket.created_at)
        date_str = now.strftime('%B %d, %Y')
        time_str = now.strftime('%I:%M:%S %p')

        escpos_data = (
            b"\x1b\x40"              # Initialize
            b"\x0c"                  # Form feed (clear buffer)
            b"\x1b\x40"              # Initialize again
            b"\x1b\x61\x01"          # Center
            
            b"\x1b\x21\x00"
            + b"COMSATS University Islamabad\n\n"
            
            b"\x1b\x21\x30"
            + ticket.display_code.encode()
            + b"\n"
            b"\x1b\x21\x00"
            + b"\n"
            
            b"\x1b\x21\x10"
            + ticket.service.name.encode()
            + b"\n"
            b"\x1b\x21\x00"
            + b"\n"
            
            b"\x1b\x61\x00"
            + b"Please wait until your number is displayed\n"
            + date_str.encode()
            + b"        "
            + time_str.encode()
            + b"\n\n\n\n\n"          # 5 line feeds
            
            b"\x1d\x56\x41\x03"      # Partial cut with 3mm spacing
        )

        win32print.WritePrinter(hPrinter, escpos_data)
        
        # Small delay to ensure command completes
        time.sleep(0.1)

        win32print.EndPagePrinter(hPrinter)
        win32print.EndDocPrinter(hPrinter)

        return True

    except Exception as e:
        logger.error(f"Windows RAW print failed: {e}")
        return False
    finally:
        # Always close the printer handle to prevent resource leaks
        if hPrinter is not None:
            try:
                win32print.ClosePrinter(hPrinter)
            except Exception:
                pass